"""Planning board REST API.

URL prefix: /api/planning

Firestore layout (subcollections under hackathons/{hid}):
  planning_lists/{list_id}
  planning_cards/{card_id}
  planning_comments/{comment_id}
  planning_labels/{label_id}
  planning_activity/{aid}   (append-only)
  planning_pending_digests/{doc_id}  (for Slack digest queue)
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from common.auth import auth, auth_user
from common.utils.firebase import get_db, get_hackathon_by_event_id
from model.planning import (
    ALLOWED_BUDGET_BUCKETS,
    ALLOWED_BUDGET_STATES,
    ALLOWED_CARD_KINDS,
    ALLOWED_CARD_STATUSES,
    ALLOWED_OUTREACH_STATUSES,
    ALLOWED_SPONSOR_TIERS,
    MAX_ATTACHMENTS_PER_CARD,
    MAX_BUDGET_CENTS,
    MAX_CARD_DESCRIPTION_LEN,
    MAX_CARD_TITLE_LEN,
    MAX_CHECKLIST_ITEMS,
    MAX_CHECKLISTS_PER_CARD,
    MAX_COMMENT_LEN,
    MAX_COMMENTS_PER_USER_PER_MIN,
    MAX_PLANNING_CARDS_PER_LIST,
    MAX_PLANNING_LABELS,
    MAX_PLANNING_LISTS,
    PLANNING_FIELD,
)
from services.hackathon_planning_service import (
    can_comment,
    is_admin,
    load_hackathon_or_404,
    require_admin_on_event,
    require_logged_in_on_enabled_plan,
    require_plan_editor,
)

logger = logging.getLogger("planning_views")

bp = Blueprint("planning", __name__, url_prefix="/api/planning")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

INTERNAL_TOKEN_HEADER = "X-Internal-Token"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_hackathon_ref(hackathon_doc):
    """Return the Firestore DocumentReference for the given hackathon dict."""
    db = get_db()
    return db.collection("hackathons").document(hackathon_doc["id"])


def _subcol(hackathon_doc, subcollection: str):
    return _get_hackathon_ref(hackathon_doc).collection(subcollection)


def _record_activity(hackathon_doc, kind: str, summary: str, actor_id: str, **extra):
    try:
        _subcol(hackathon_doc, "planning_activity").add({
            "kind": kind,
            "summary": summary,
            "actor": actor_id,
            "created_at": _now_iso(),
            **extra,
        })
    except Exception:
        logger.exception("Failed to write activity record")


def _check_if_match(doc_dict):
    """Return 412 response tuple if If-Match header doesn't match updated_at."""
    expected = request.headers.get("If-Match")
    if expected and doc_dict.get("updated_at") != expected:
        return jsonify({"error": "Conflict", "updated_at": doc_dict.get("updated_at")}), 412
    return None


def _enqueue_slack_digest(hackathon_doc, event_data: dict):
    """Append a pending Slack digest event; triggers lazy flush on the next write after deadline."""
    try:
        planning = hackathon_doc.get(PLANNING_FIELD) or {}
        slack = planning.get("slack") or {}
        if not slack.get("notify_on_card_change"):
            return
        if request.get_json(silent=True, force=True) and request.get_json(silent=True, force=True).get("notify_slack") is False:
            return
        _subcol(hackathon_doc, "planning_pending_digests").add({
            "created_at": _now_iso(),
            **event_data,
        })
    except Exception:
        logger.exception("Failed to enqueue Slack digest event")


def _compute_etag(lists_docs, cards_docs, labels_docs) -> str:
    updated_ats = []
    for d in lists_docs + cards_docs + labels_docs:
        ua = d.get("updated_at") or d.get("created_at") or ""
        updated_ats.append(ua)
    updated_ats.sort()
    payload = "|".join(updated_ats).encode()
    return hashlib.sha1(payload).hexdigest()


# ---------------------------------------------------------------------------
# Rate limit (comment spam) — Redis-backed when available, else no-op
# ---------------------------------------------------------------------------

def _check_comment_rate_limit(user_id: str):
    """Return 429 response if user exceeds MAX_COMMENTS_PER_USER_PER_MIN."""
    try:
        from common.utils.redis_cache import get_redis_client
        rc = get_redis_client()
        if rc is None:
            return None
        key = f"planning:comment_rate:{user_id}"
        count = rc.incr(key)
        if count == 1:
            rc.expire(key, 60)
        if count > MAX_COMMENTS_PER_USER_PER_MIN:
            return jsonify({"error": "Too many comments"}), 429
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Board snapshot
# ---------------------------------------------------------------------------

@bp.route("/<event_id>", methods=["GET"])
def get_board(event_id):
    """Public board snapshot: lists + cards + labels. ETag-based caching."""
    hackathon = load_hackathon_or_404(event_id)
    planning = hackathon.get(PLANNING_FIELD) or {}
    if not planning.get("enabled"):
        return jsonify({"enabled": False}), 200

    hid = hackathon["id"]
    db = get_db()
    href = db.collection("hackathons").document(hid)

    lists_docs = sorted(
        [{**d.to_dict(), "id": d.id} for d in href.collection("planning_lists").where("archived", "==", False).stream()],
        key=lambda x: x.get("position", ""),
    )
    cards_docs = sorted(
        [{**d.to_dict(), "id": d.id} for d in href.collection("planning_cards").where("archived", "==", False).stream()],
        key=lambda x: x.get("position", ""),
    )
    labels_docs = [
        {**d.to_dict(), "id": d.id}
        for d in href.collection("planning_labels").stream()
    ]

    etag = _compute_etag(lists_docs, cards_docs, labels_docs)
    if request.headers.get("If-None-Match") == etag:
        return "", 304

    # Lazy Slack digest flush when past deadline
    _maybe_flush_digests_read_driven(hackathon)

    # Resolve display profiles for everyone referenced in cards.assignees + planning.editors
    # so the frontend can render avatars without per-user round-trips.
    referenced_ids = set()
    for c in cards_docs:
        referenced_ids.update(c.get("assignees") or [])
    referenced_ids.update(planning.get("editors") or [])
    users_map = _resolve_public_user_profiles(referenced_ids)

    resp = jsonify({
        "event_id": event_id,
        "planning": planning,
        "lists": lists_docs,
        "cards": cards_docs,
        "labels": labels_docs,
        "users": users_map,
    })
    resp.headers["ETag"] = etag
    return resp, 200


# Cache resolved user profiles briefly so the board snapshot doesn't pay for
# fetch_users() on every poll. 60s is short enough that name/avatar changes
# show up quickly while cutting the read load by ~10x for an active board.
_USER_PROFILES_CACHE = {"profiles": None, "expires_at": 0}


def _resolve_public_user_profiles(propel_ids):
    """Return {propel_id: {name, profile_image, nickname}} for the given set.

    Public-safe fields only. Returns {} when the set is empty so the call
    site can skip the work.
    """
    import time
    if not propel_ids:
        return {}

    now = time.time()
    if not _USER_PROFILES_CACHE["profiles"] or _USER_PROFILES_CACHE["expires_at"] < now:
        try:
            from db.db import fetch_users
            all_users = fetch_users() or []
            indexed = {}
            for u in all_users:
                pid = getattr(u, "user_id", None)
                if not pid:
                    continue
                indexed[pid] = {
                    "name": getattr(u, "name", "") or getattr(u, "nickname", "") or "",
                    "nickname": getattr(u, "nickname", "") or "",
                    "profile_image": getattr(u, "profile_image", "") or "",
                }
            _USER_PROFILES_CACHE["profiles"] = indexed
            _USER_PROFILES_CACHE["expires_at"] = now + 60
        except Exception:
            logger.exception("Failed to resolve user profiles for board snapshot")
            return {}

    indexed = _USER_PROFILES_CACHE["profiles"] or {}
    return {pid: indexed[pid] for pid in propel_ids if pid in indexed}


def _maybe_flush_digests_read_driven(hackathon_doc):
    """Best-effort read-driven Slack digest flush (runs on GET board)."""
    try:
        from services.planning_slack_notifier import flush_digests_if_due
        flush_digests_if_due(hackathon_doc)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/lists", methods=["POST"])
@require_plan_editor()
def create_list(event_id):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    existing = list(_subcol(g.hackathon, "planning_lists").where("archived", "==", False).stream())
    if len(existing) >= MAX_PLANNING_LISTS:
        return jsonify({"error": f"Max {MAX_PLANNING_LISTS} lists reached"}), 400

    now = _now_iso()
    position = data.get("position") or f"p{len(existing):06d}"
    doc_ref = _subcol(g.hackathon, "planning_lists").document()
    doc = {
        "title": title,
        "position": position,
        "archived": False,
        "is_run_of_show": bool(data.get("is_run_of_show", False)),
        "created_at": now,
        "updated_at": now,
    }
    doc_ref.set(doc)
    _record_activity(g.hackathon, "list_created", f'Created list "{title}"', g.propel_user_id, list_id=doc_ref.id)
    return jsonify({"id": doc_ref.id, **doc}), 201


@bp.route("/<event_id>/lists/<list_id>", methods=["PATCH"])
@require_plan_editor()
def update_list(event_id, list_id):
    list_ref = _subcol(g.hackathon, "planning_lists").document(list_id)
    snap = list_ref.get()
    if not snap.exists:
        return jsonify({"error": "List not found"}), 404
    existing = snap.to_dict()

    conflict = _check_if_match(existing)
    if conflict:
        return conflict

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": _now_iso()}
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        updates["title"] = title
    if "position" in data:
        updates["position"] = data["position"]
    if "archived" in data:
        updates["archived"] = bool(data["archived"])
    if "is_run_of_show" in data:
        updates["is_run_of_show"] = bool(data["is_run_of_show"])

    list_ref.update(updates)
    return jsonify({"id": list_id, **existing, **updates}), 200


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/cards", methods=["POST"])
@require_plan_editor()
def create_card(event_id):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:MAX_CARD_TITLE_LEN]
    if not title:
        return jsonify({"error": "title required"}), 400
    list_id = data.get("list_id")
    if not list_id:
        return jsonify({"error": "list_id required"}), 400

    list_snap = _subcol(g.hackathon, "planning_lists").document(list_id).get()
    if not list_snap.exists:
        return jsonify({"error": "List not found"}), 404

    existing_cards = list(
        _subcol(g.hackathon, "planning_cards")
        .where("list_id", "==", list_id)
        .where("archived", "==", False)
        .stream()
    )
    if len(existing_cards) >= MAX_PLANNING_CARDS_PER_LIST:
        return jsonify({"error": f"Max {MAX_PLANNING_CARDS_PER_LIST} cards per list reached"}), 400

    kind = data.get("kind", "freetext")
    if kind not in ALLOWED_CARD_KINDS:
        return jsonify({"error": f"Invalid kind: {kind}"}), 400

    now = _now_iso()
    position = data.get("position") or f"p{len(existing_cards):06d}"
    doc_ref = _subcol(g.hackathon, "planning_cards").document()
    doc = {
        "list_id": list_id,
        "title": title,
        "description": (data.get("description") or "")[:MAX_CARD_DESCRIPTION_LEN],
        "kind": kind,
        "assignees": data.get("assignees") or [],
        "labels": data.get("labels") or [],
        "due_date": data.get("due_date"),
        "position": position,
        "archived": False,
        "checklists": [],
        "attachments": [],
        "comment_count": 0,
        "created_by": g.propel_user_id,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        # Run of Show fields
        "start_time": data.get("start_time"),
        "end_time": data.get("end_time"),
        "sync_to_countdowns": bool(data.get("sync_to_countdowns", True)),
        # Budget field (optional)
        "budget": _validate_budget(data.get("budget")),
        # Kind-specific fields
        "target_count": data.get("target_count"),
        "sponsor": _validate_sponsor(data.get("sponsor")) if kind == "sponsor_prospect" else None,
    }
    doc_ref.set(doc)
    _record_activity(g.hackathon, "card_created", f'Created card "{title}"', g.propel_user_id, card_id=doc_ref.id, list_id=list_id)
    _enqueue_slack_digest(g.hackathon, {"kind": "card_created", "card_id": doc_ref.id, "card_title": title, "list_id": list_id})
    return jsonify({"id": doc_ref.id, **doc}), 201


def _validate_budget(budget):
    if not budget:
        return None
    if not isinstance(budget, dict):
        return None
    amount = budget.get("amount_cents")
    bucket = budget.get("bucket")
    state = budget.get("state", "estimated")
    if not isinstance(amount, int) or amount < 0 or amount > MAX_BUDGET_CENTS:
        return None
    if bucket not in ALLOWED_BUDGET_BUCKETS:
        return None
    if state not in ALLOWED_BUDGET_STATES:
        state = "estimated"
    return {
        "amount_cents": amount,
        "bucket": bucket,
        "state": state,
        "vendor": (budget.get("vendor") or "")[:200],
        "invoice_url": budget.get("invoice_url"),
    }


def _validate_sponsor(sponsor):
    if not sponsor or not isinstance(sponsor, dict):
        return None
    tier = sponsor.get("tier", "tbd")
    if tier not in ALLOWED_SPONSOR_TIERS:
        tier = "tbd"
    status = sponsor.get("outreach_status", "prospect")
    if status not in ALLOWED_OUTREACH_STATUSES:
        status = "prospect"
    pledge = sponsor.get("pledge_amount_cents", 0)
    if not isinstance(pledge, int) or pledge < 0:
        pledge = 0
    return {
        "company": (sponsor.get("company") or "")[:200],
        "logo_url": sponsor.get("logo_url"),
        "website": sponsor.get("website"),
        "tier": tier,
        "outreach_status": status,
        "point_of_contact_internal": sponsor.get("point_of_contact_internal"),
        "point_of_contact_external": sponsor.get("point_of_contact_external"),
        "pledge_amount_cents": pledge,
        "last_contacted_at": sponsor.get("last_contacted_at"),
        "next_action": (sponsor.get("next_action") or "")[:500],
    }


@bp.route("/<event_id>/cards/<card_id>", methods=["PATCH"])
@require_plan_editor()
def update_card(event_id, card_id):
    card_ref = _subcol(g.hackathon, "planning_cards").document(card_id)
    snap = card_ref.get()
    if not snap.exists:
        return jsonify({"error": "Card not found"}), 404
    existing = snap.to_dict()

    conflict = _check_if_match(existing)
    if conflict:
        return conflict

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": _now_iso(), "last_activity_at": _now_iso()}

    if "title" in data:
        title = (data["title"] or "").strip()[:MAX_CARD_TITLE_LEN]
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        updates["title"] = title

    if "description" in data:
        updates["description"] = (data["description"] or "")[:MAX_CARD_DESCRIPTION_LEN]

    if "list_id" in data:
        list_snap = _subcol(g.hackathon, "planning_lists").document(data["list_id"]).get()
        if not list_snap.exists:
            return jsonify({"error": "Target list not found"}), 404
        updates["list_id"] = data["list_id"]

    if "position" in data:
        updates["position"] = data["position"]

    if "archived" in data:
        updates["archived"] = bool(data["archived"])

    if "assignees" in data:
        updates["assignees"] = data["assignees"] if isinstance(data["assignees"], list) else []

    if "labels" in data:
        updates["labels"] = data["labels"] if isinstance(data["labels"], list) else []

    if "due_date" in data:
        updates["due_date"] = data["due_date"]

    if "kind" in data:
        kind = data["kind"]
        if kind not in ALLOWED_CARD_KINDS:
            return jsonify({"error": f"Invalid kind: {kind}"}), 400
        updates["kind"] = kind

    if "status" in data:
        status = data["status"]
        # null/empty clears the status (back to "no status set")
        if status in (None, "", "none"):
            updates["status"] = None
        elif status in ALLOWED_CARD_STATUSES:
            updates["status"] = status
        else:
            return jsonify({"error": f"Invalid status: {status}"}), 400

    if "checklists" in data:
        checklists = data["checklists"]
        if not isinstance(checklists, list) or len(checklists) > MAX_CHECKLISTS_PER_CARD:
            return jsonify({"error": "Invalid checklists"}), 400
        for cl in checklists:
            if not isinstance(cl.get("items"), list) or len(cl["items"]) > MAX_CHECKLIST_ITEMS:
                return jsonify({"error": "Invalid checklist items"}), 400
        updates["checklists"] = checklists

    if "attachments" in data:
        attachments = data["attachments"]
        if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS_PER_CARD:
            return jsonify({"error": f"Max {MAX_ATTACHMENTS_PER_CARD} attachments per card"}), 400
        updates["attachments"] = attachments

    if "budget" in data:
        updates["budget"] = _validate_budget(data["budget"])

    if "sponsor" in data:
        updates["sponsor"] = _validate_sponsor(data["sponsor"])

    if "target_count" in data:
        updates["target_count"] = data["target_count"]

    # Run of Show fields
    if "start_time" in data:
        updates["start_time"] = data["start_time"]
    if "end_time" in data:
        updates["end_time"] = data["end_time"]
    if "sync_to_countdowns" in data:
        updates["sync_to_countdowns"] = bool(data["sync_to_countdowns"])

    card_ref.update(updates)
    _enqueue_slack_digest(g.hackathon, {
        "kind": "card_updated",
        "card_id": card_id,
        "card_title": updates.get("title", existing.get("title", "")),
        "list_id": updates.get("list_id", existing.get("list_id", "")),
    })
    return jsonify({"id": card_id, **existing, **updates}), 200


@bp.route("/<event_id>/cards/<card_id>", methods=["DELETE"])
@require_plan_editor()
def archive_card(event_id, card_id):
    card_ref = _subcol(g.hackathon, "planning_cards").document(card_id)
    snap = card_ref.get()
    if not snap.exists:
        return jsonify({"error": "Card not found"}), 404
    now = _now_iso()
    card_ref.update({"archived": True, "updated_at": now})
    _record_activity(g.hackathon, "card_archived", f'Archived card "{snap.to_dict().get("title", "")}"', g.propel_user_id, card_id=card_id)
    return jsonify({"id": card_id, "archived": True}), 200


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/cards/<card_id>/comments", methods=["GET"])
def get_comments(event_id, card_id):
    hackathon = load_hackathon_or_404(event_id)
    planning = hackathon.get(PLANNING_FIELD) or {}
    if not planning.get("enabled"):
        return jsonify({"error": "Planning board not enabled"}), 404

    comments = [
        {**d.to_dict(), "id": d.id}
        for d in _subcol(hackathon, "planning_comments")
        .where("card_id", "==", card_id)
        .order_by("created_at")
        .stream()
        if not d.to_dict().get("deleted_at")
    ]
    return jsonify({"comments": comments}), 200


@bp.route("/<event_id>/cards/<card_id>/comments", methods=["POST"])
@require_logged_in_on_enabled_plan()
def create_comment(event_id, card_id):
    rate_limit = _check_comment_rate_limit(g.propel_user_id)
    if rate_limit:
        return rate_limit

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body required"}), 400
    if len(body) > MAX_COMMENT_LEN:
        return jsonify({"error": f"body exceeds {MAX_COMMENT_LEN} chars"}), 400

    card_ref = _subcol(g.hackathon, "planning_cards").document(card_id)
    card_snap = card_ref.get()
    if not card_snap.exists:
        return jsonify({"error": "Card not found"}), 404

    now = _now_iso()
    author_info = {"user_id": g.propel_user_id}
    try:
        if auth_user:
            author_info["name"] = getattr(auth_user, "first_name", "") + " " + getattr(auth_user, "last_name", "")
            author_info["name"] = author_info["name"].strip()
    except Exception:
        pass

    doc_ref = _subcol(g.hackathon, "planning_comments").document()
    comment = {
        "card_id": card_id,
        "body": body,
        "author": author_info,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    doc_ref.set(comment)

    # Increment comment_count
    from google.cloud.firestore import Increment
    card_ref.update({"comment_count": Increment(1), "last_activity_at": now})

    _record_activity(g.hackathon, "comment_added", "Added a comment", g.propel_user_id, card_id=card_id)

    # Best-effort @-mention notifications. Failures here must not break the
    # comment write; the comment is already persisted above.
    try:
        from services.planning_mention_notifier import (
            parse_mention_ids,
            notify_mentions,
        )
        mentioned = parse_mention_ids(body)
        if mentioned:
            card_data = card_snap.to_dict() or {}
            notify_mentions(
                mentioned_propel_ids=mentioned,
                actor_propel_id=g.propel_user_id,
                actor_name=author_info.get("name") or "Someone",
                hackathon_event_id=event_id,
                card_title=card_data.get("title", "(untitled)"),
                card_id=card_id,
                comment_body=body,
            )
    except Exception:
        logger.exception("Mention notification dispatch failed")

    return jsonify({"id": doc_ref.id, **comment}), 201


@bp.route("/<event_id>/comments/<comment_id>", methods=["DELETE"])
@auth.require_user
def delete_comment(event_id, comment_id):
    hackathon = load_hackathon_or_404(event_id)
    planning = hackathon.get(PLANNING_FIELD) or {}
    if not planning.get("enabled"):
        return jsonify({"error": "Planning board not enabled"}), 404

    comment_ref = _subcol(hackathon, "planning_comments").document(comment_id)
    snap = comment_ref.get()
    if not snap.exists:
        return jsonify({"error": "Comment not found"}), 404

    comment = snap.to_dict()
    author_id = (comment.get("author") or {}).get("user_id")

    # admin OR comment author OR card creator can delete
    if not is_admin(auth_user) and auth_user.user_id != author_id:
        return jsonify({"error": "Forbidden"}), 403

    now = _now_iso()
    comment_ref.update({"deleted_at": now, "body": "[deleted]"})
    return jsonify({"id": comment_id, "deleted_at": now}), 200


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/labels", methods=["POST"])
@require_plan_editor()
def create_label(event_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    color = (data.get("color") or "#888888").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    existing = list(_subcol(g.hackathon, "planning_labels").stream())
    if len(existing) >= MAX_PLANNING_LABELS:
        return jsonify({"error": f"Max {MAX_PLANNING_LABELS} labels reached"}), 400

    now = _now_iso()
    doc_ref = _subcol(g.hackathon, "planning_labels").document()
    label = {"name": name, "color": color, "created_at": now, "updated_at": now}
    doc_ref.set(label)
    return jsonify({"id": doc_ref.id, **label}), 201


@bp.route("/<event_id>/labels/<label_id>", methods=["PATCH"])
@require_plan_editor()
def update_label(event_id, label_id):
    label_ref = _subcol(g.hackathon, "planning_labels").document(label_id)
    snap = label_ref.get()
    if not snap.exists:
        return jsonify({"error": "Label not found"}), 404

    data = request.get_json(silent=True) or {}
    updates = {"updated_at": _now_iso()}
    if "name" in data:
        updates["name"] = (data["name"] or "").strip()
    if "color" in data:
        updates["color"] = (data["color"] or "").strip()

    label_ref.update(updates)
    return jsonify({"id": label_id, **snap.to_dict(), **updates}), 200


# ---------------------------------------------------------------------------
# Editors (admin only)
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/editors", methods=["PATCH"])
@require_admin_on_event()
def update_editors(event_id):
    data = request.get_json(silent=True) or {}
    add_ids = data.get("add") or []
    remove_ids = data.get("remove") or []

    if not isinstance(add_ids, list) or not isinstance(remove_ids, list):
        return jsonify({"error": "add and remove must be arrays"}), 400

    db = get_db()
    href = _get_hackathon_ref(g.hackathon)
    snap = href.get()
    current = snap.to_dict() or {}
    planning = current.get(PLANNING_FIELD) or {}
    editors = list(planning.get("editors") or [])

    for uid in add_ids:
        if isinstance(uid, str) and uid not in editors:
            editors.append(uid)
    for uid in remove_ids:
        if uid in editors:
            editors.remove(uid)

    planning["editors"] = editors
    href.update({PLANNING_FIELD: planning})
    return jsonify({"editors": editors}), 200


# ---------------------------------------------------------------------------
# Planning config (admin only — enable/disable, slack settings, budget widget)
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/config", methods=["PATCH"])
@require_admin_on_event()
def update_planning_config(event_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    href = _get_hackathon_ref(g.hackathon)
    snap = href.get()
    current = snap.to_dict() or {}
    planning = dict(current.get(PLANNING_FIELD) or {})

    if "enabled" in data:
        planning["enabled"] = bool(data["enabled"])
    if "budget_widget_on_event_page" in data:
        planning["budget_widget_on_event_page"] = bool(data["budget_widget_on_event_page"])
    if "slack" in data:
        slack = data["slack"]
        if isinstance(slack, dict):
            current_slack = dict(planning.get("slack") or {})
            if "channel" in slack:
                current_slack["channel"] = (slack["channel"] or "").lstrip("#").strip()[:80]
            if "notify_on_card_change" in slack:
                current_slack["notify_on_card_change"] = bool(slack["notify_on_card_change"])
            planning["slack"] = current_slack

    href.update({PLANNING_FIELD: planning})
    return jsonify({"planning": planning}), 200


# ---------------------------------------------------------------------------
# Seed template (admin only)
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/seed-template", methods=["POST"])
@require_admin_on_event()
def seed_template(event_id):
    planning = g.hackathon.get(PLANNING_FIELD) or {}
    if planning.get("template_seeded"):
        return jsonify({"message": "Template already seeded"}), 200

    from services.planning_template_service import apply_ohack_template
    apply_ohack_template(g.hackathon)

    href = _get_hackathon_ref(g.hackathon)
    planning["template_seeded"] = True
    href.update({PLANNING_FIELD: planning})

    _record_activity(g.hackathon, "template_seeded", "Applied OHack default template", g.propel_user_id)
    return jsonify({"message": "Template applied"}), 200


# ---------------------------------------------------------------------------
# @-mention search — any logged-in user can search for someone to mention
# in a comment. Returns minimal public-safe fields only (no email, no Slack
# ID), so this can never be used to harvest PII.
# ---------------------------------------------------------------------------

@bp.route("/_users/mention-search", methods=["GET"])
@auth.require_user
def mention_search_users():
    """Substring match across cached user list. Public-safe fields only.

    Min 2 chars (avoids dumping the user table). Capped at 10 results.
    Reuses the same 60s cache as the board snapshot's user resolver.
    """
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"users": []}), 200

    # Force a populate of _USER_PROFILES_CACHE if cold by calling with a
    # placeholder — cheaper than duplicating the fetch logic.
    _resolve_public_user_profiles({"__warm__"})  # noqa
    indexed = _USER_PROFILES_CACHE.get("profiles") or {}

    out = []
    for pid, profile in indexed.items():
        name = (profile.get("name") or "").lower()
        nickname = (profile.get("nickname") or "").lower()
        if q in name or q in nickname:
            out.append({
                "user_id": pid,
                "name": profile.get("name") or profile.get("nickname") or "",
                "profile_image": profile.get("profile_image") or "",
            })
            if len(out) >= 10:
                break

    return jsonify({"users": out}), 200


# ---------------------------------------------------------------------------
# Admin user search — for the editors picker (no PropelAuth ID memorization)
# ---------------------------------------------------------------------------

@bp.route("/_users/search", methods=["GET"])
@auth.require_user
def search_users_for_editor_picker():
    """Substring match across Firestore users by name/email/nickname.

    Admin-only. Returns up to 25 candidates with the propel user_id needed
    by the editors[] list. Q is required and at least 2 chars to avoid
    dumping the whole user table.
    """
    if not is_admin(auth_user):
        return jsonify({"error": "Forbidden"}), 403

    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"users": []}), 200

    from db.db import fetch_users
    try:
        all_users = fetch_users() or []
    except Exception:
        logger.exception("fetch_users failed")
        return jsonify({"users": []}), 200

    results = []
    for u in all_users:
        propel_id = getattr(u, "user_id", None)
        if not propel_id:
            continue
        name = (getattr(u, "name", None) or "").lower()
        nickname = (getattr(u, "nickname", None) or "").lower()
        email = (getattr(u, "email_address", None) or "").lower()
        if q in name or q in nickname or q in email:
            results.append({
                "user_id": propel_id,
                "name": getattr(u, "name", None) or getattr(u, "nickname", None) or "",
                "email": getattr(u, "email_address", None) or "",
                "profile_image": getattr(u, "profile_image", None) or "",
            })
            if len(results) >= 25:
                break

    return jsonify({"users": results}), 200


# ---------------------------------------------------------------------------
# Advisory editing heartbeat (Redis-backed, gracefully degraded)
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/cards/<card_id>/editing-heartbeat", methods=["POST"])
@auth.require_user
def editing_heartbeat(event_id, card_id):
    hackathon = load_hackathon_or_404(event_id)
    planning = hackathon.get(PLANNING_FIELD) or {}
    if not planning.get("enabled"):
        return jsonify({"error": "Planning board not enabled"}), 404

    try:
        from common.utils.redis_cache import get_redis_client
        rc = get_redis_client()
        if rc:
            key = f"planning:card:editing:{card_id}:{auth_user.user_id}"
            rc.set(key, 1, ex=30)
            # Collect all editors of this card
            pattern = f"planning:card:editing:{card_id}:*"
            editors = [k.decode().split(":")[-1] for k in rc.keys(pattern)]
            return jsonify({"editors": editors}), 200
    except Exception:
        pass

    return jsonify({"editors": []}), 200


# ---------------------------------------------------------------------------
# Slack manual digest
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/slack/notify", methods=["POST"])
@require_plan_editor()
def slack_notify(event_id):
    try:
        from services.planning_slack_notifier import send_manual_digest
        send_manual_digest(g.hackathon)
        return jsonify({"message": "Digest sent"}), 200
    except Exception as e:
        logger.exception("Manual Slack digest failed")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Internal cron: flush pending Slack digests
# ---------------------------------------------------------------------------

@bp.route("/_flush_digests", methods=["POST"])
def flush_digests_cron():
    """Called by Cloud Scheduler / GitHub Actions cron. Authenticated via X-Internal-Token."""
    import os
    expected = os.environ.get("PLANNING_INTERNAL_TOKEN")
    # Require the token when set; if not set, only allow requests from localhost
    if expected:
        if request.headers.get(INTERNAL_TOKEN_HEADER) != expected:
            return jsonify({"error": "Forbidden"}), 403
    else:
        # No token configured — restrict to loopback to prevent accidental public exposure
        remote = request.remote_addr or ""
        if remote not in ("127.0.0.1", "::1", "localhost"):
            return jsonify({"error": "PLANNING_INTERNAL_TOKEN not configured"}), 403

    from services.planning_slack_notifier import flush_all_pending_digests
    try:
        count = flush_all_pending_digests()
        return jsonify({"flushed": count}), 200
    except Exception as e:
        logger.exception("flush_digests_cron failed")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Run of Show — preview + sync
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LexoRank rebalance (editor — triggered when gap < threshold)
# ---------------------------------------------------------------------------

@bp.route("/<event_id>/lists/<list_id>/rebalance", methods=["POST"])
@require_plan_editor()
def rebalance_list(event_id, list_id):
    """Reissue evenly-spaced positions for all cards in a list (batch write).

    Called when the client detects position gap exhaustion. Each card in the
    list gets a new position string; the batch commits atomically so concurrent
    readers always see a consistent sort order.

    Cross-event safety: the parent list is fetched from g.hackathon (stashed by
    the decorator from the URL's event_id), not from the request body.
    """
    db = get_db()
    href = _get_hackathon_ref(g.hackathon)

    # Verify the list belongs to this hackathon
    list_snap = href.collection("planning_lists").document(list_id).get()
    if not list_snap.exists:
        return jsonify({"error": "List not found"}), 404

    # Load all non-archived cards in the list, sorted by current position
    cards_query = (
        href.collection("planning_cards")
        .where("list_id", "==", list_id)
        .where("archived", "==", False)
        .order_by("position")
        .stream()
    )
    cards = [(d.id, d.to_dict()) for d in cards_query]

    if not cards:
        return jsonify({"rebalanced": 0}), 200

    # Issue evenly-spaced positions across [1000, 9999000] with step = 9998000/(n+1)
    n = len(cards)
    step = max(1, 9998000 // (n + 1))
    now = _now_iso()

    batch = db.batch()
    new_positions = []
    for i, (card_id, _) in enumerate(cards):
        new_pos = f"p{(step * (i + 1)):07d}"
        new_positions.append(new_pos)
        card_ref = href.collection("planning_cards").document(card_id)
        batch.update(card_ref, {"position": new_pos, "updated_at": now})

    batch.commit()

    _record_activity(
        g.hackathon,
        "list_rebalanced",
        f"Rebalanced positions in list (n={n})",
        g.propel_user_id,
        list_id=list_id,
    )
    return jsonify({"rebalanced": n, "positions": new_positions}), 200


@bp.route("/<event_id>/run-of-show/preview", methods=["GET"])
@require_plan_editor()
def ros_preview(event_id):
    from services.planning_ros_service import compute_ros_diff
    diff = compute_ros_diff(g.hackathon)
    return jsonify(diff), 200


@bp.route("/<event_id>/run-of-show/sync", methods=["POST"])
@require_plan_editor()
def ros_sync(event_id):
    from services.planning_ros_service import sync_ros_to_countdowns
    result = sync_ros_to_countdowns(g.hackathon, actor_id=g.propel_user_id)
    _record_activity(
        g.hackathon,
        "ros_synced",
        f"Synced {result['synced']} Run of Show entries to the public timeline",
        g.propel_user_id,
    )
    _enqueue_slack_digest(g.hackathon, {"kind": "ros_synced", "count": result["synced"]})
    return jsonify(result), 200
