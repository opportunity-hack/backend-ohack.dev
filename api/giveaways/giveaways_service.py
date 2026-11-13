"""Giveaways v2 — campaigns with prizes, per-prize entries, a recorded draw.

Collections (see README.md in this folder):
  giveaway_campaigns/{giveaway_id}            — doc id = slug, immutable
  giveaway_entries/{giveaway_id}__{user_db_id} — ONE entry per person per giveaway

The legacy `giveaways` collection and `/api/messages/giveaway*` routes are left
alone for old clients; nothing here reads or writes them.

Entry weight is computed HERE, never trusted from the client:
  5 per filled profile field (GIVEAWAY_PROFILE_FIELDS) + GitHub contributions
  (commits + PRs + issues + reviews) capped at GITHUB_ENTRY_CAP.

The winner is chosen by a pure, seeded function on the frontend
(src/lib/giveawayDraw.js). This module stores the seed and validates + records
each result (one prize per person, one winner per slot, the person entered
that prize).
"""
import threading
import uuid
from datetime import datetime, timezone

from cachetools import TTLCache, cached

from common.log import get_logger
from common.utils.slack import send_slack_audit
from common.utils.validators import (
    parse_offset_iso,
    validate_giveaway_campaign,
    validate_giveaway_campaign_partial,
)
from db.db import get_db

logger = get_logger("api.giveaways.giveaways_service")

CAMPAIGNS_COLLECTION = "giveaway_campaigns"
ENTRIES_COLLECTION = "giveaway_entries"

# Keep in sync with the frontend's src/components/Profile/profileFields.js.
# Scored: the basic profile, Instagram + LinkedIn, the mailing address (prizes
# ship to it) and the public-portfolio fields (/profile#portfolio). Not scored:
# apartment/suite and company (a blank is often correct), the stickers
# preference and the system-set propel_id. profile_visibility is deliberately
# NOT scored — portfolios are private-first and entries must never pressure
# anyone into going public.
GIVEAWAY_PROFILE_FIELDS = (
    "role", "github", "expertise", "education", "why", "shirt_size",
    "linkedin_url", "instagram_url",
    "street_address", "city", "state", "postal_code", "country",
    "profile_slug", "headline", "bio", "bio_video_url", "portfolio_links",
)
ENTRIES_PER_PROFILE_FIELD = 5
GITHUB_ENTRY_CAP = 45
MAX_SEED_LENGTH = 64
MAX_PICKED_PRIZES = 50
OUTCOMES = ("won", "absent")

_campaign_cache = TTLCache(maxsize=1, ttl=10)
_campaign_cache_lock = threading.Lock()


class GiveawayError(Exception):
    def __init__(self, status, error, message):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


# ---------------------------------------------------------------------------
# Seams (tests replace these)
# ---------------------------------------------------------------------------

def _get_db():
    return get_db()


def _now():
    return datetime.now(timezone.utc)


def _new_id():
    return uuid.uuid4().hex


def _resolve_user(propel_id):
    """The canonical own-profile resolver (GET /api/users/profile uses it)."""
    from services.users_service import _resolve_and_ensure_user
    user, _ = _resolve_and_ensure_user(propel_id)
    return user


def _github_history(login):
    """Same data as GET /api/messages/profile/github/<username>."""
    from common.utils.firebase import get_github_contributions_for_user
    return get_github_contributions_for_user(login)


def _in_transaction(db, body):
    """Runs body(transaction) in a real Firestore transaction. Tests replace
    this with `lambda db, body: body(FakeTransaction())`."""
    from firebase_admin import firestore

    @firestore.transactional
    def _run(transaction):
        return body(transaction)

    return _run(db.transaction())


def _tx_get(transaction, ref):
    """transaction.get(ref) yields snapshots for a DocumentReference."""
    result = transaction.get(ref)
    if hasattr(result, "exists"):
        return result
    return next(iter(result), None)


def _audit(action, message, payload=None):
    try:
        send_slack_audit(action=action, message=message, payload=payload)
    except Exception:  # never fail a write on an audit hiccup
        logger.exception("giveaway slack audit failed")


# ---------------------------------------------------------------------------
# Weight
# ---------------------------------------------------------------------------

def _filled(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _as_int(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    if isinstance(value, dict):
        return _as_int(value.get("total"))
    return 0


def github_contribution_count(history):
    """commits + pull requests + issues + reviews across every repo."""
    total = 0
    for row in history or []:
        if not isinstance(row, dict):
            continue
        total += (
            _as_int(row.get("commits"))
            + _as_int(row.get("pull_requests"))
            + _as_int(row.get("issues"))
            + _as_int(row.get("reviews"))
        )
    return total


def compute_weight(profile_values, github_contributions):
    """(entries, breakdown) from a {field: value} mapping and a raw GitHub
    contribution count. Profile entries are uncapped; GitHub is capped."""
    filled = sum(1 for name in GIVEAWAY_PROFILE_FIELDS if _filled(profile_values.get(name)))
    profile_entries = filled * ENTRIES_PER_PROFILE_FIELD
    github_entries = min(max(int(github_contributions or 0), 0), GITHUB_ENTRY_CAP)
    breakdown = {
        "profile_fields": filled,
        "profile_fields_total": len(GIVEAWAY_PROFILE_FIELDS),
        "profile_entries": profile_entries,
        "github_contributions": max(int(github_contributions or 0), 0),
        "github_entries": github_entries,
        "github_cap": GITHUB_ENTRY_CAP,
    }
    return profile_entries + github_entries, breakdown


def _profile_values(user):
    return {name: getattr(user, name, None) for name in GIVEAWAY_PROFILE_FIELDS}


def _weigh_user(user, previous_breakdown=None):
    """(entries, breakdown, github_unavailable). A GitHub lookup failure never
    fails the entry: it falls back to the previously stored GitHub entries."""
    login = (getattr(user, "github", None) or "").strip()
    github_unavailable = False
    contributions = 0
    if login:
        try:
            contributions = github_contribution_count(_github_history(login))
        except Exception:
            logger.exception("giveaway github lookup failed for %s", login)
            github_unavailable = True
            contributions = int((previous_breakdown or {}).get("github_entries") or 0)
    entries, breakdown = compute_weight(_profile_values(user), contributions)
    return entries, breakdown, github_unavailable


def _user_snapshot(user):
    return {
        "name": getattr(user, "name", None) or "",
        "nickname": getattr(user, "nickname", None) or "",
        "profile_image": getattr(user, "profile_image", None) or "",
        "github": getattr(user, "github", None) or "",
        "email": getattr(user, "email_address", None) or "",
    }


# ---------------------------------------------------------------------------
# Campaign reads
# ---------------------------------------------------------------------------

@cached(cache=_campaign_cache, lock=_campaign_cache_lock)
def _all_campaigns():
    """Every campaign doc (a handful per year). 10s TTL (read_after_write — see
    common/utils/cache_registry.py), cleared on writes."""
    out = []
    for doc in _get_db().collection(CAMPAIGNS_COLLECTION).stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        out.append(data)
    return out


def clear_cache():
    with _campaign_cache_lock:
        _campaign_cache.clear()


def _campaign_ref(giveaway_id):
    return _get_db().collection(CAMPAIGNS_COLLECTION).document(giveaway_id)


def _read_campaign(giveaway_id):
    snap = _campaign_ref(giveaway_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _sorted_prizes(campaign):
    prizes = [p for p in (campaign.get("prizes") or []) if isinstance(p, dict)]
    return sorted(prizes, key=lambda p: p.get("sort_order", 0))


def _is_accepting(campaign, now=None):
    if campaign.get("status") != "open":
        return False
    closes = parse_offset_iso(campaign.get("closes_at"))
    if closes is not None and (now or _now()) >= closes:
        return False
    return True


def _public_campaign(campaign):
    return {
        "id": campaign.get("id"),
        "title": campaign.get("title") or "",
        "description": campaign.get("description") or "",
        "event_id": campaign.get("event_id"),
        "closes_at": campaign.get("closes_at"),
        "must_be_present": campaign.get("must_be_present", True) is not False,
        "status": campaign.get("status") or "draft",
        "accepting_entries": _is_accepting(campaign),
        "prizes": _sorted_prizes(campaign),
    }


def _results(campaign):
    return [r for r in ((campaign.get("draw") or {}).get("results") or []) if isinstance(r, dict)]


def _lean_admin_campaign(campaign):
    view = _public_campaign(campaign)
    draw = campaign.get("draw") or {}
    results = _results(campaign)
    view.update({
        "created_at": campaign.get("created_at"),
        "updated_at": campaign.get("updated_at"),
        "draw_started": bool(draw.get("seed")),
        "winners_count": sum(1 for r in results if r.get("outcome") == "won"),
        "slots_total": sum(int(p.get("quantity") or 1) for p in _sorted_prizes(campaign)),
    })
    return view


def _public_entry(entry):
    if not entry:
        return None
    return {
        "all_prizes": bool(entry.get("all_prizes")),
        "prize_ids": list(entry.get("prize_ids") or []),
        "entries": int(entry.get("entries") or 0),
        "breakdown": entry.get("breakdown") or {},
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }


def _entry_doc_id(giveaway_id, user_db_id):
    return f"{giveaway_id}__{user_db_id}"


# ---------------------------------------------------------------------------
# Participant routes
# ---------------------------------------------------------------------------

def get_open_giveaways(propel_id):
    """GET /api/giveaways/open — open campaigns plus closed/drawn ones the
    caller entered, each with the caller's entry + win, and the caller's
    current weight (so the tab can show "your entries" before entering)."""
    user = _resolve_user(propel_id)
    if user is None or not getattr(user, "id", None):
        raise GiveawayError(503, "profile_unavailable", "We couldn't load your profile just now.")

    mine = {}
    for doc in _get_db().collection(ENTRIES_COLLECTION).where("user_id", "==", user.id).stream():
        data = doc.to_dict() or {}
        if data.get("giveaway_id"):
            mine[data["giveaway_id"]] = data

    previous = None
    if mine:
        previous = max(mine.values(), key=lambda e: e.get("updated_at") or "").get("breakdown")
    entries, breakdown, github_unavailable = _weigh_user(user, previous)

    giveaways = []
    for campaign in _all_campaigns():
        status = campaign.get("status")
        entered = campaign["id"] in mine
        if status != "open" and not (entered and status in ("closed", "drawn")):
            continue
        view = _public_campaign(campaign)
        view["my_entry"] = _public_entry(mine.get(campaign["id"]))
        view["my_win"] = None
        prizes_by_id = {p.get("id"): p for p in view["prizes"]}
        for result in _results(campaign):
            if result.get("user_id") == user.id and result.get("outcome") == "won":
                prize = prizes_by_id.get(result.get("prize_id")) or {}
                view["my_win"] = {
                    "prize_id": result.get("prize_id"),
                    "name": prize.get("name") or "",
                    "image_url": prize.get("image_url"),
                }
        giveaways.append(view)

    giveaways.sort(key=lambda g: (g["status"] != "open", g.get("closes_at") or "~"))
    return {
        "giveaways": giveaways,
        "my_weight": {
            "entries": entries,
            "breakdown": breakdown,
            "github_unavailable": github_unavailable,
        },
    }


def _clean_picks(campaign, body):
    if not isinstance(body, dict):
        raise GiveawayError(400, "invalid_body", "Send {all_prizes, prize_ids}.")
    all_prizes = body.get("all_prizes", False)
    if not isinstance(all_prizes, bool):
        raise GiveawayError(400, "invalid_body", "all_prizes must be true or false.")
    prize_ids = body.get("prize_ids") or []
    if not isinstance(prize_ids, list) or len(prize_ids) > MAX_PICKED_PRIZES or not all(
        isinstance(p, str) for p in prize_ids
    ):
        raise GiveawayError(400, "invalid_prizes", "prize_ids must be a list of prize ids.")
    known = {p.get("id") for p in _sorted_prizes(campaign)}
    if not known:
        return True, []
    if all_prizes:
        return True, []
    unknown = [p for p in prize_ids if p not in known]
    if unknown:
        raise GiveawayError(400, "invalid_prizes", "One of those prizes isn't part of this giveaway.")
    picked = [p.get("id") for p in _sorted_prizes(campaign) if p.get("id") in set(prize_ids)]
    if not picked:
        raise GiveawayError(400, "no_prizes_selected", "Pick at least one prize.")
    return False, picked


def save_entry(propel_id, giveaway_id, body):
    """POST /api/giveaways/<id>/entry — upsert the caller's one entry."""
    campaign = _read_campaign(giveaway_id)
    if campaign is None or campaign.get("status") == "draft":
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    if not _is_accepting(campaign):
        raise GiveawayError(409, "giveaway_closed", "Entries for this giveaway are closed.")
    all_prizes, prize_ids = _clean_picks(campaign, body)

    user = _resolve_user(propel_id)
    if user is None or not getattr(user, "id", None):
        raise GiveawayError(503, "profile_unavailable", "We couldn't load your profile just now.")

    ref = _get_db().collection(ENTRIES_COLLECTION).document(_entry_doc_id(giveaway_id, user.id))
    existing_snap = ref.get()
    existing = existing_snap.to_dict() if existing_snap.exists else None
    entries, breakdown, github_unavailable = _weigh_user(user, (existing or {}).get("breakdown"))
    now_iso = _now().isoformat()
    doc = {
        "giveaway_id": giveaway_id,
        "user_id": user.id,
        "all_prizes": all_prizes,
        "prize_ids": prize_ids,
        "entries": entries,
        "breakdown": breakdown,
        "user": _user_snapshot(user),
        "created_at": (existing or {}).get("created_at") or now_iso,
        "updated_at": now_iso,
    }
    ref.set(doc)
    entry = _public_entry(doc)
    entry["github_unavailable"] = github_unavailable
    return {"entry": entry, "created": existing is None}


def withdraw_entry(propel_id, giveaway_id):
    """DELETE /api/giveaways/<id>/entry."""
    campaign = _read_campaign(giveaway_id)
    if campaign is None or campaign.get("status") == "draft":
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    if not _is_accepting(campaign):
        raise GiveawayError(409, "giveaway_closed", "Entries for this giveaway are closed.")
    user = _resolve_user(propel_id)
    if user is None or not getattr(user, "id", None):
        raise GiveawayError(503, "profile_unavailable", "We couldn't load your profile just now.")
    ref = _get_db().collection(ENTRIES_COLLECTION).document(_entry_doc_id(giveaway_id, user.id))
    existed = ref.get().exists
    if existed:
        ref.delete()
    return {"withdrawn": existed}


# ---------------------------------------------------------------------------
# Admin: campaigns
# ---------------------------------------------------------------------------

def admin_list_campaigns():
    campaigns = [_lean_admin_campaign(c) for c in _all_campaigns()]
    campaigns.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return {"giveaways": campaigns}


def admin_get_campaign(giveaway_id):
    campaign = _read_campaign(giveaway_id)
    if campaign is None:
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    view = _lean_admin_campaign(campaign)
    draw = campaign.get("draw") or {}
    view["draw"] = {
        "seed": draw.get("seed"),
        "started_at": draw.get("started_at"),
        "started_by": draw.get("started_by"),
        "results": _results(campaign),
    } if draw else None
    return view


def admin_create_campaign(body, actor):
    try:
        giveaway_id, clean = validate_giveaway_campaign(body, _new_id)
    except ValueError as e:
        raise GiveawayError(400, "invalid_campaign", str(e))
    ref = _campaign_ref(giveaway_id)
    if ref.get().exists:
        raise GiveawayError(409, "giveaway_exists", "A giveaway with that id already exists.")
    now_iso = _now().isoformat()
    doc = {
        "title": clean["title"],
        "description": clean.get("description", ""),
        "event_id": clean.get("event_id"),
        "status": clean.get("status", "draft"),
        "must_be_present": clean.get("must_be_present", True),
        "closes_at": clean.get("closes_at"),
        "prizes": clean.get("prizes", []),
        "draw": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "updated_by": actor,
    }
    ref.set(doc)
    clear_cache()
    _audit("giveaway_create", f"Giveaway created: {giveaway_id} ({doc['title']})")
    return admin_get_campaign(giveaway_id)


def admin_update_campaign(giveaway_id, body, actor):
    try:
        clean = validate_giveaway_campaign_partial(body, _new_id)
    except ValueError as e:
        raise GiveawayError(400, "invalid_campaign", str(e))
    campaign = _read_campaign(giveaway_id)
    if campaign is None:
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")

    draw_started = bool((campaign.get("draw") or {}).get("seed"))
    if "status" in clean and clean["status"] != campaign.get("status") and draw_started:
        raise GiveawayError(
            409, "draw_started",
            "The draw has started, so the status can't change. Reset the draw first.",
        )

    if "prizes" in clean:
        won_slots = {}
        for result in _results(campaign):
            if result.get("outcome") == "won":
                won_slots.setdefault(result.get("prize_id"), set()).add(int(result.get("slot") or 0))
        new_by_id = {p["id"]: p for p in clean["prizes"]}
        for prize_id, slots in won_slots.items():
            prize = new_by_id.get(prize_id)
            if prize is None or prize["quantity"] <= max(slots):
                raise GiveawayError(
                    409, "prize_has_winner",
                    "A prize that already has a winner can't be removed or reduced below its winners.",
                )

    clean["updated_at"] = _now().isoformat()
    clean["updated_by"] = actor
    _campaign_ref(giveaway_id).set(clean, merge=True)
    clear_cache()
    return admin_get_campaign(giveaway_id)


def admin_list_entries(giveaway_id):
    """ONE query, zero profile reads — the entry carries its user snapshot."""
    entries = []
    query = _get_db().collection(ENTRIES_COLLECTION).where("giveaway_id", "==", giveaway_id)
    for doc in query.stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        entries.append(data)
    entries.sort(key=lambda e: e.get("user_id") or "")
    return {"entries": entries, "count": len(entries)}


MAX_REMOVE_ENTRY_IDS = 500


def admin_remove_entries(giveaway_id, body, actor):
    """POST /admin/<id>/entries/remove — `{entry_ids: [...]}` or `{all: true}`.

    Refused once a draw has started: the stage ranks the entry list it loaded
    at Start, and a recorded winner must keep the entry it won with. Reset the
    draw first. People can enter again while the giveaway is open."""
    body = body if isinstance(body, dict) else {}
    remove_all = body.get("all") is True
    ids = body.get("entry_ids")
    if not remove_all:
        if (
            not isinstance(ids, list) or not ids or len(ids) > MAX_REMOVE_ENTRY_IDS
            or not all(isinstance(i, str) and i for i in ids)
        ):
            raise GiveawayError(
                400, "invalid_entry_ids",
                f"Send entry_ids (1-{MAX_REMOVE_ENTRY_IDS}) or all: true.",
            )
    campaign = _read_campaign(giveaway_id)
    if campaign is None:
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    if (campaign.get("draw") or {}).get("seed"):
        raise GiveawayError(
            409, "draw_started",
            "The draw has started, so entries can't be removed. Reset the draw first.",
        )
    collection = _get_db().collection(ENTRIES_COLLECTION)
    removed = []
    if remove_all:
        for doc in collection.where("giveaway_id", "==", giveaway_id).stream():
            collection.document(doc.id).delete()
            removed.append(doc.id)
    else:
        for entry_id in dict.fromkeys(ids):
            ref = collection.document(entry_id)
            snap = ref.get()
            data = snap.to_dict() if snap.exists else None
            # Only this giveaway's entries — an id from another campaign is ignored.
            if not data or data.get("giveaway_id") != giveaway_id:
                continue
            ref.delete()
            removed.append(entry_id)
    if removed:
        scope = "all entries" if remove_all else f"{len(removed)} entr{'y' if len(removed) == 1 else 'ies'}"
        _audit(
            "giveaway_entries_removed",
            f"Giveaway entries removed: {giveaway_id} ({scope}, {len(removed)} total) by {actor}",
            {"giveaway_id": giveaway_id, "entry_ids": removed[:50], "all": remove_all},
        )
    out = admin_list_entries(giveaway_id)
    out["removed"] = len(removed)
    return out


# ---------------------------------------------------------------------------
# Admin: the draw
# ---------------------------------------------------------------------------

def _all_slots_won(campaign, results):
    won = {(r.get("prize_id"), int(r.get("slot") or 0)) for r in results if r.get("outcome") == "won"}
    for prize in _sorted_prizes(campaign):
        for slot in range(int(prize.get("quantity") or 1)):
            if (prize.get("id"), slot) not in won:
                return False
    return True


def admin_start_draw(giveaway_id, body, actor):
    seed = (body or {}).get("seed") if isinstance(body, dict) else None
    if not isinstance(seed, str) or not seed.strip() or len(seed.strip()) > MAX_SEED_LENGTH:
        raise GiveawayError(400, "invalid_seed", f"Send a seed of 1-{MAX_SEED_LENGTH} characters.")
    seed = seed.strip()
    campaign = _read_campaign(giveaway_id)
    if campaign is None:
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    existing = (campaign.get("draw") or {}).get("seed")
    if existing:
        if existing != seed:
            raise GiveawayError(
                409, "draw_already_started",
                f"This draw already started with seed {existing}. Resume it or reset it.",
            )
        return admin_get_campaign(giveaway_id)
    if campaign.get("status") == "draft":
        raise GiveawayError(409, "giveaway_draft", "Open the giveaway before drawing.")
    _campaign_ref(giveaway_id).set({
        "status": "closed",
        "draw": {"seed": seed, "started_at": _now().isoformat(), "started_by": actor, "results": []},
        "updated_at": _now().isoformat(),
        "updated_by": actor,
    }, merge=True)
    clear_cache()
    _audit("giveaway_draw_start", f"Giveaway draw started: {giveaway_id} (seed {seed})")
    return admin_get_campaign(giveaway_id)


def _check_result(campaign, results, entry, body):
    prize_id = body.get("prize_id")
    prizes = {p.get("id"): p for p in _sorted_prizes(campaign)}
    prize = prizes.get(prize_id)
    if prize is None:
        raise GiveawayError(400, "invalid_prize", "That prize isn't part of this giveaway.")
    slot = body.get("slot")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < int(prize.get("quantity") or 1):
        raise GiveawayError(400, "invalid_slot", "That prize doesn't have that many winners.")
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        raise GiveawayError(400, "invalid_outcome", "outcome must be won or absent.")
    user_id = body.get("user_id")
    if not entry:
        raise GiveawayError(400, "not_entered", "That person didn't enter this giveaway.")
    if not entry.get("all_prizes") and prize_id not in (entry.get("prize_ids") or []):
        raise GiveawayError(400, "not_entered", "That person didn't enter for this prize.")
    if int(entry.get("entries") or 0) <= 0:
        raise GiveawayError(400, "not_entered", "That person has no entries.")
    if outcome == "won":
        for r in results:
            if r.get("outcome") != "won":
                continue
            if r.get("user_id") == user_id:
                raise GiveawayError(409, "already_won", "That person already won a prize in this giveaway.")
            if r.get("prize_id") == prize_id and int(r.get("slot") or 0) == slot:
                raise GiveawayError(409, "slot_filled", "That prize already has a winner.")
    return prize, slot, outcome


def admin_record_result(giveaway_id, body, actor):
    """POST draw/results. `{finish: true}` (with or without a result) marks
    the giveaway `drawn`; it also becomes `drawn` when every slot is won."""
    if not isinstance(body, dict):
        raise GiveawayError(400, "invalid_body", "Send a result.")
    has_result = body.get("prize_id") is not None
    finish = body.get("finish") is True
    if not has_result and not finish:
        raise GiveawayError(400, "invalid_body", "Send a result or {finish: true}.")
    db = _get_db()
    campaign_ref = db.collection(CAMPAIGNS_COLLECTION).document(giveaway_id)
    user_id = body.get("user_id")
    if has_result and (not isinstance(user_id, str) or not user_id):
        raise GiveawayError(400, "invalid_user", "Send the user_id of the person drawn.")
    entry_ref = (
        db.collection(ENTRIES_COLLECTION).document(_entry_doc_id(giveaway_id, user_id))
        if has_result else None
    )
    recorded = {}

    def body_fn(transaction):
        snap = _tx_get(transaction, campaign_ref)
        if snap is None or not snap.exists:
            raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
        campaign = snap.to_dict() or {}
        draw = campaign.get("draw") or {}
        if not draw.get("seed"):
            raise GiveawayError(409, "draw_not_started", "Start the draw first.")
        results = _results(campaign)
        if has_result:
            entry_snap = _tx_get(transaction, entry_ref)
            entry = entry_snap.to_dict() if entry_snap is not None and entry_snap.exists else None
            prize, slot, outcome = _check_result(campaign, results, entry, body)
            attempt = body.get("attempt")
            result = {
                "id": _new_id(),
                "prize_id": prize.get("id"),
                "slot": slot,
                "user_id": user_id,
                "name": ((entry or {}).get("user") or {}).get("name") or "",
                "profile_image": ((entry or {}).get("user") or {}).get("profile_image") or "",
                "outcome": outcome,
                "attempt": attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else 0,
                "at": _now().isoformat(),
                "by": actor,
            }
            results = results + [result]
            recorded["result"] = result
            recorded["prize_name"] = prize.get("name") or ""
        status = campaign.get("status")
        if finish or _all_slots_won(campaign, results):
            status = "drawn"
        draw = dict(draw, results=results)
        transaction.set(campaign_ref, {
            "draw": draw,
            "status": status,
            "updated_at": _now().isoformat(),
            "updated_by": actor,
        }, merge=True)
        recorded["status"] = status

    _in_transaction(db, body_fn)
    clear_cache()
    result = recorded.get("result")
    if result and result["outcome"] == "won":
        _audit(
            "giveaway_winner",
            f"Giveaway {giveaway_id}: {result['name'] or result['user_id']} won {recorded.get('prize_name')}",
        )
    return {"result": result, "status": recorded.get("status"), "giveaway": admin_get_campaign(giveaway_id)}


def admin_undo_result(giveaway_id, body, actor):
    result_id = (body or {}).get("result_id") if isinstance(body, dict) else None
    if not isinstance(result_id, str) or not result_id:
        raise GiveawayError(400, "invalid_body", "Send the result_id to undo.")
    db = _get_db()
    campaign_ref = db.collection(CAMPAIGNS_COLLECTION).document(giveaway_id)

    def body_fn(transaction):
        snap = _tx_get(transaction, campaign_ref)
        if snap is None or not snap.exists:
            raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
        campaign = snap.to_dict() or {}
        results = _results(campaign)
        kept = [r for r in results if r.get("id") != result_id]
        if len(kept) == len(results):
            raise GiveawayError(404, "result_not_found", "That result isn't in this draw.")
        status = campaign.get("status")
        if status == "drawn" and not _all_slots_won(campaign, kept):
            status = "closed"
        transaction.set(campaign_ref, {
            "draw": dict(campaign.get("draw") or {}, results=kept),
            "status": status,
            "updated_at": _now().isoformat(),
            "updated_by": actor,
        }, merge=True)

    _in_transaction(db, body_fn)
    clear_cache()
    return admin_get_campaign(giveaway_id)


def admin_reset_draw(giveaway_id, actor):
    campaign = _read_campaign(giveaway_id)
    if campaign is None:
        raise GiveawayError(404, "not_found", "That giveaway doesn't exist.")
    _campaign_ref(giveaway_id).set({
        "draw": None,
        "status": "closed",
        "updated_at": _now().isoformat(),
        "updated_by": actor,
    }, merge=True)
    clear_cache()
    won = sum(1 for r in _results(campaign) if r.get("outcome") == "won")
    _audit("giveaway_draw_reset", f"Giveaway draw reset: {giveaway_id} ({won} recorded winners cleared)")
    return admin_get_campaign(giveaway_id)
