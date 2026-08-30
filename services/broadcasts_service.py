"""Resend segment sync + broadcast + batch-send service for the admin Email tab.

Ports scripts/sync_resend_audience.py into the app: collect contacts from
Firestore (users/volunteers/leads) and Slack, sync them into a Resend segment
(create-only, idempotent), and send one broadcast to the segment instead of
N per-recipient emails. Also hosts the transactional Batch send used by the
frontend's personalized bulk path (100 emails per Resend call).

API keys: segment/contact/broadcast operations REQUIRE the full-access
RESEND_API_KEY (RESEND_WELCOME_EMAIL_KEY is send-only and 401s on them — never
fall back to it here). Batch sends are Emails-scope and use the welcome key.

Hazard: resend.api_key is a module-level global shared with every other email
path across gunicorn threads. Each entry point here sets it immediately before
its Resend calls and keeps the call section short; a long-running sync thread
re-sets it defensively per write.
"""

import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import markdown
import resend

from api.messages.message import Message
from common.log import get_logger
from common.utils.firebase import get_db
from common.utils.redis_cache import delete_cached, get_cached, set_cached
from common.utils.slack import send_slack_audit

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
QR_MARKER_RE = re.compile(r"\[QRCode:", re.IGNORECASE)
UNSUBSCRIBE_PLACEHOLDER = "{{{RESEND_UNSUBSCRIBE_URL}}}"

MAX_BATCH_RECIPIENTS_PER_REQUEST = 500
RESEND_BATCH_CHUNK = 100  # Resend /emails/batch hard limit

_SYNC_STATUS_TTL = 24 * 3600
_SYNC_LOCK_TTL = 30 * 60  # self-heals if the worker dies mid-sync
_SYNC_STALL_SECONDS = 120
_CONTACT_WRITE_SLEEP = 0.05  # proven value from scripts/sync_resend_audience.py

_CONTACTS_CACHE_KEY = "broadcasts:contacts:index"
_CONTACTS_CACHE_TTL = 60
_PRUNE_STATUS_KEY = "broadcasts:contacts:prune:status"
_PRUNE_LOCK_KEY = "broadcasts:contacts:prune:lock"
_PRUNE_LOCK_TTL = 30 * 60
PRUNE_MODES = ("unsubscribed", "emails", "all")

VOLUNTEER_TYPES = ("mentor", "judge", "sponsor", "volunteer", "hacker")

DEFAULT_BROADCAST_FROM = "Opportunity Hack <updates@notify.ohack.dev>"
DEFAULT_BROADCAST_FROM_DOMAINS = "notify.ohack.dev,apply.ohack.dev"
DEFAULT_REPLY_TO = "Opportunity Hack Questions <questions@ohack.org>"


def _sync_status_key(segment_id: str) -> str:
    return f"broadcasts:sync:{segment_id}:status"


def _sync_lock_key(segment_id: str) -> str:
    return f"broadcasts:sync:{segment_id}:lock"


def _notifications_disabled() -> bool:
    """Mirror of the ENVIRONMENT=test gate used across services — unit tests
    (MockFirestore) must never write to Resend or send email."""
    return os.environ.get("ENVIRONMENT") == "test"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contact_limit() -> int:
    """Marketing-tier contact cap used for over-limit warnings (Resend free
    marketing tier = 1,000 contacts). Raise via env after upgrading the tier."""
    try:
        return int(os.environ.get("RESEND_MARKETING_CONTACT_LIMIT", "1000"))
    except ValueError:
        return 1000


def _resend_full_key() -> str:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise RuntimeError(
            "RESEND_API_KEY (full-access) is not configured — required for "
            "segment/contact/broadcast operations"
        )
    return key


def _resend_send_key() -> str:
    key = os.environ.get("RESEND_WELCOME_EMAIL_KEY") or os.environ.get("RESEND_API_KEY")
    if not key:
        raise RuntimeError("RESEND_WELCOME_EMAIL_KEY is not configured")
    return key


def _segments_api():
    """Prefer the Segments API; older SDKs only expose the Audiences alias
    (same server-side objects and ids)."""
    return getattr(resend, "Segments", None) or resend.Audiences


def _resp_data(resp) -> list:
    if isinstance(resp, dict):
        return resp.get("data", []) or []
    return getattr(resp, "data", []) or []


def _field(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


# ---------------------------------------------------------------------------
# Contact collection (ported from scripts/sync_resend_audience.py)
# ---------------------------------------------------------------------------

def _norm_email(raw) -> Optional[str]:
    if not raw:
        return None
    e = str(raw).strip().lower()
    return e if EMAIL_RE.match(e) else None


def _split_name(full: str) -> Tuple[str, str]:
    if not full:
        return "", ""
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _add(contacts: Dict[str, dict], email: Optional[str], first: str, last: str, src: str):
    """Insert or upgrade a contact entry. Prefers entries with a real name."""
    if not email:
        return
    first = (first or "").strip()
    last = (last or "").strip()
    existing = contacts.get(email)
    if existing is None:
        contacts[email] = {
            "email": email,
            "first_name": first,
            "last_name": last,
            "source": src,
        }
        return
    if not existing["first_name"] and first:
        existing["first_name"] = first
    if not existing["last_name"] and last:
        existing["last_name"] = last


def load_profiles() -> Dict[str, dict]:
    """Registered ohack.dev users (users collection)."""
    db = get_db()
    out: Dict[str, dict] = {}
    for doc in db.collection("users").stream():
        d = doc.to_dict() or {}
        email = _norm_email(d.get("email_address"))
        if not email:
            continue
        first, last = _split_name(d.get("name", "") or d.get("nickname", ""))
        _add(out, email, first, last, "profiles")
    return out


def load_volunteers(volunteer_type: Optional[str], event_id: Optional[str],
                    selected_only: bool) -> Dict[str, dict]:
    db = get_db()
    query = db.collection("volunteers")
    if volunteer_type:
        query = query.where("volunteer_type", "==", volunteer_type)
    if event_id:
        query = query.where("event_id", "==", event_id)
    # isSelected filtered in Python — a firestore where would need a composite index.
    out: Dict[str, dict] = {}
    for doc in query.stream():
        d = doc.to_dict() or {}
        if selected_only and not d.get("isSelected"):
            continue
        email = _norm_email(d.get("email"))
        if not email:
            continue
        first = d.get("first_name", "") or ""
        last = d.get("last_name", "") or ""
        if not first and not last:
            first, last = _split_name(d.get("name", ""))
        _add(out, email, first, last, f"volunteers:{volunteer_type or 'all'}")
    return out


def load_leads() -> Dict[str, dict]:
    """Newsletter signups (leads collection, fed by the ohack.dev LeadForm)."""
    db = get_db()
    out: Dict[str, dict] = {}
    for doc in db.collection("leads").stream():
        d = doc.to_dict() or {}
        email = _norm_email(d.get("email"))
        if not email:
            continue
        first, last = _split_name(d.get("name", ""))
        _add(out, email, first, last, "leads")
    return out


def load_contact_submissions(inquiry_types: Optional[List[str]] = None,
                             updates_opt_in_only: bool = False) -> Dict[str, dict]:
    """Contact-form submitters (contact_submissions collection, /contact page).

    inquiry_types: keep only these `inquiryType` values (case-insensitive);
    empty/None = all. updates_opt_in_only: keep only submitters who checked
    the form's `receiveUpdates` box."""
    wanted = {t.strip().lower() for t in (inquiry_types or []) if t and t.strip()}
    db = get_db()
    out: Dict[str, dict] = {}
    for doc in db.collection("contact_submissions").stream():
        d = doc.to_dict() or {}
        if wanted and (d.get("inquiryType") or "").strip().lower() not in wanted:
            continue
        if updates_opt_in_only and not d.get("receiveUpdates"):
            continue
        email = _norm_email(d.get("email"))
        if not email:
            continue
        first = (d.get("firstName") or "").strip()
        last = (d.get("lastName") or "").strip()
        if not first and not last:
            first, last = _split_name(d.get("name", ""))
        _add(out, email, first, last, "contact_submissions")
    return out


def load_slack_members(active_days: int = 365) -> Dict[str, dict]:
    """Slack workspace members with emails. Deleted/disabled/bot/restricted
    accounts are already excluded inside get_active_users."""
    from api.slack.slack_service import get_active_users  # lazy: avoid import at module load

    out: Dict[str, dict] = {}
    for u in get_active_users(days=active_days, admin=True):
        email = _norm_email(u.get("email"))
        if not email:
            continue
        first, last = _split_name(u.get("real_name") or u.get("name") or "")
        _add(out, email, first, last, "slack")
    return out


def collect_contacts(sources: List[dict], custom_emails: Optional[List[str]] = None
                     ) -> Tuple[Dict[str, dict], dict]:
    """Resolve a sources spec into a deduped contact dict keyed by lowercase
    email, plus per-source stats for the preview UI.

    Source spec entries:
      {"type": "profiles"}
      {"type": "leads"}
      {"type": "volunteers", "volunteer_type"?: str, "event_id"?: str, "selected_only"?: bool}
      {"type": "slack", "active_days"?: int}
      {"type": "contact_submissions", "inquiry_types"?: [str], "updates_opt_in_only"?: bool}
    """
    combined: Dict[str, dict] = {}
    per_source: Dict[str, int] = {}
    raw_total = 0

    for spec in sources or []:
        stype = (spec or {}).get("type")
        if stype == "profiles":
            chunk = load_profiles()
            label = "profiles"
        elif stype == "leads":
            chunk = load_leads()
            label = "leads"
        elif stype == "volunteers":
            vtype = spec.get("volunteer_type") or None
            if vtype and vtype not in VOLUNTEER_TYPES:
                raise ValueError(f"unknown volunteer_type: {vtype}")
            chunk = load_volunteers(vtype, spec.get("event_id") or None,
                                    bool(spec.get("selected_only")))
            label = f"volunteers:{vtype or 'all'}"
            if spec.get("event_id"):
                label += f":{spec['event_id']}"
        elif stype == "contact_submissions":
            inquiry_types = spec.get("inquiry_types") or []
            if not isinstance(inquiry_types, list):
                raise ValueError("inquiry_types must be a list")
            chunk = load_contact_submissions(
                inquiry_types, bool(spec.get("updates_opt_in_only")))
            label = "contact:" + (
                ",".join(sorted(t.strip().lower() for t in inquiry_types if t.strip()))
                or "all")
            if spec.get("updates_opt_in_only"):
                label += ":opted-in"
        elif stype == "slack":
            try:
                active_days = int(spec.get("active_days", 365))
            except (TypeError, ValueError):
                active_days = 365
            active_days = min(max(active_days, 1), 10000)
            chunk = load_slack_members(active_days)
            label = f"slack:{active_days}d"
        else:
            raise ValueError(f"unknown source type: {stype}")

        per_source[label] = len(chunk)
        raw_total += len(chunk)
        for email, rec in chunk.items():
            _add(combined, email, rec["first_name"], rec["last_name"], rec["source"])

    custom_valid = 0
    custom_invalid: List[str] = []
    for raw in custom_emails or []:
        email = _norm_email(raw)
        if not email:
            custom_invalid.append(str(raw))
            continue
        custom_valid += 1
        raw_total += 1
        _add(combined, email, "", "", "custom")

    stats = {
        "per_source": per_source,
        "custom_valid": custom_valid,
        "custom_invalid": custom_invalid,
        "union_total": len(combined),
        "overlap_removed": raw_total - len(combined),
        "contact_limit": _contact_limit(),
        "over_limit": len(combined) > _contact_limit(),
    }
    return combined, stats


def preview_sources(payload: dict) -> Tuple[Message, int]:
    """Dry-run: counts only, no Resend reads or writes."""
    payload = payload or {}
    try:
        _, stats = collect_contacts(payload.get("sources"), payload.get("custom_emails"))
    except ValueError as e:
        return Message(str(e)), 400
    msg = Message("Preview computed")
    msg.stats = stats
    return msg, 200


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

def list_segments() -> Tuple[Message, int]:
    resend.api_key = _resend_full_key()
    listed = _segments_api().list()
    segments = [
        {
            "id": _field(a, "id"),
            "name": _field(a, "name"),
            "created_at": _field(a, "created_at"),
        }
        for a in _resp_data(listed)
    ]
    msg = Message("ok")
    msg.segments = segments
    return msg, 200


def _get_or_create_segment(name: str) -> str:
    """List-then-create by name; reuses standing segments across sends."""
    api = _segments_api()
    for a in _resp_data(api.list()):
        if _field(a, "name") == name:
            return _field(a, "id")
    created = api.create({"name": name})
    segment_id = _field(created, "id")
    logger.info("created Resend segment '%s' id=%s", name, segment_id)
    return segment_id


def _existing_segment_emails(segment_id: str) -> Set[str]:
    """Full paginated email set already in the segment → idempotent re-syncs."""
    out: Set[str] = set()
    after = None
    while True:
        params: dict = {"limit": 100}
        if after:
            params["after"] = after
        # audience_id is the compat kwarg — segments and audiences share ids.
        resp = resend.Contacts.list(audience_id=segment_id, params=params)
        data = _resp_data(resp)
        if not data:
            break
        last_id = None
        for c in data:
            email = _field(c, "email")
            if email:
                out.add(email.strip().lower())
            last_id = _field(c, "id")
        if len(data) < 100 or not last_id:
            break
        after = last_id
    return out


# ---------------------------------------------------------------------------
# Contact management (quota lives on GLOBAL contacts — deleting a contact
# account-wide is what frees marketing-tier quota; unsubscribed contacts
# can't receive broadcasts but STILL count against the limit)
# ---------------------------------------------------------------------------

def _serialize_contact(c) -> dict:
    return {
        "id": _field(c, "id"),
        "email": (_field(c, "email") or "").strip().lower(),
        "first_name": _field(c, "first_name") or "",
        "last_name": _field(c, "last_name") or "",
        "unsubscribed": bool(_field(c, "unsubscribed")),
        "created_at": _field(c, "created_at"),
    }


def _crawl_contacts_page(after: Optional[str]):
    params: dict = {"limit": 100}
    if after:
        params["after"] = after
    try:
        # Account-level (global) contacts — the set the quota counts.
        return resend.Contacts.list(params=params)
    except TypeError:
        # Very old SDKs require audience_id; fall back to the union across
        # segments (may miss contacts in no segment, but better than nothing).
        return None


def _crawl_all_contacts() -> List[dict]:
    out: Dict[str, dict] = {}
    after = None
    while True:
        resp = _crawl_contacts_page(after)
        if resp is None:
            # Fallback: union of every segment's contacts.
            for seg in _resp_data(_segments_api().list()):
                seg_id = _field(seg, "id")
                seg_after = None
                while True:
                    params = {"limit": 100}
                    if seg_after:
                        params["after"] = seg_after
                    seg_resp = resend.Contacts.list(audience_id=seg_id, params=params)
                    data = _resp_data(seg_resp)
                    if not data:
                        break
                    for c in data:
                        rec = _serialize_contact(c)
                        if rec["email"]:
                            out.setdefault(rec["email"], rec)
                    if len(data) < 100:
                        break
                    seg_after = _field(data[-1], "id")
            break
        data = _resp_data(resp)
        if not data:
            break
        for c in data:
            rec = _serialize_contact(c)
            if rec["email"]:
                out.setdefault(rec["email"], rec)
        if len(data) < 100:
            break
        after = _field(data[-1], "id")
    return sorted(out.values(), key=lambda r: r["email"])


def list_contacts(force: bool = False) -> Tuple[Message, int]:
    """Full contact inventory (cached 60s) + quota picture for the admin UI."""
    contacts = None if force else get_cached(_CONTACTS_CACHE_KEY)
    if contacts is None:
        resend.api_key = _resend_full_key()
        contacts = _crawl_all_contacts()
        set_cached(_CONTACTS_CACHE_KEY, contacts, ttl=_CONTACTS_CACHE_TTL)

    unsubscribed = sum(1 for c in contacts if c.get("unsubscribed"))
    msg = Message("ok")
    msg.contacts = contacts
    msg.total = len(contacts)
    msg.unsubscribed_count = unsubscribed
    msg.contact_limit = _contact_limit()
    msg.over_limit = len(contacts) > _contact_limit()
    return msg, 200


def start_contact_prune(payload: dict, actor: Optional[dict]) -> Tuple[Message, int]:
    """Delete GLOBAL contacts to reclaim marketing-tier quota. Modes:
    - "unsubscribed": every unsubscribed contact (safe — they can't receive
      broadcasts anyway, but they count against the limit)
    - "emails": an explicit list (admin-selected in the UI)
    - "all": everything (danger — the UI requires typed confirmation)
    Runs in a daemon thread (one global job at a time) with polled status."""
    payload = payload or {}
    mode = (payload.get("mode") or "").strip()
    if mode not in PRUNE_MODES:
        return Message(f"mode must be one of {', '.join(PRUNE_MODES)}"), 400

    if get_cached(_PRUNE_LOCK_KEY) is not None:
        msg = Message("A contact prune is already running")
        msg.status = "already_running"
        return msg, 409

    resend.api_key = _resend_full_key()
    contacts = _crawl_all_contacts()

    if mode == "unsubscribed":
        targets = [c["email"] for c in contacts if c.get("unsubscribed")]
    elif mode == "all":
        targets = [c["email"] for c in contacts]
    else:
        requested = {e for e in ((_norm_email(x) for x in payload.get("emails") or [])) if e}
        if not requested:
            return Message("emails is required for mode=emails"), 400
        known = {c["email"] for c in contacts}
        targets = sorted(requested & known)

    if not targets:
        msg = Message("Nothing to delete for the requested mode")
        msg.status = "empty"
        msg.total_targets = 0
        return msg, 200

    set_cached(_PRUNE_LOCK_KEY, True, ttl=_PRUNE_LOCK_TTL)
    status = {
        "state": "running",
        "mode": mode,
        "total_targets": len(targets),
        "deleted": 0,
        "failed": 0,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "finished_at": None,
        "error": None,
        "requested_by": (actor or {}).get("email"),
        "simulated": _notifications_disabled(),
    }
    set_cached(_PRUNE_STATUS_KEY, status, ttl=_SYNC_STATUS_TTL)

    threading.Thread(
        target=_run_contact_prune,
        args=(mode, targets, actor),
        daemon=True,
    ).start()

    msg = Message("Contact prune started")
    msg.status = "started"
    msg.mode = mode
    msg.total_targets = len(targets)
    return msg, 202


def _update_prune_status(patch: dict) -> dict:
    status = get_cached(_PRUNE_STATUS_KEY) or {}
    status.update(patch)
    status["updated_at"] = _now_iso()
    set_cached(_PRUNE_STATUS_KEY, status, ttl=_SYNC_STATUS_TTL)
    return status


def _run_contact_prune(mode: str, targets: List[str], actor: Optional[dict]) -> None:
    try:
        if _notifications_disabled():
            _update_prune_status({"state": "done", "finished_at": _now_iso(),
                                  "simulated": True})
            return

        deleted, failed = 0, 0
        for email in targets:
            try:
                resend.api_key = _resend_full_key()
                # No audience_id → deletes the GLOBAL contact (frees quota).
                resend.Contacts.remove(email=email)
                deleted += 1
            except Exception as e:
                failed += 1
                logger.warning("contact prune: failed to delete %s: %s", email, e)
            if (deleted + failed) % 25 == 0:
                _update_prune_status({"deleted": deleted, "failed": failed})
            time.sleep(_CONTACT_WRITE_SLEEP)

        _update_prune_status({
            "state": "done",
            "deleted": deleted,
            "failed": failed,
            "finished_at": _now_iso(),
        })
        send_slack_audit(
            action="broadcast_contact_prune",
            message=f"Resend contact prune finished: mode={mode} "
                    f"deleted={deleted} failed={failed} of {len(targets)}",
            payload={"requested_by": (actor or {}).get("email")},
        )
    except Exception as e:
        logger.error("contact prune failed: %s", e)
        _update_prune_status({"state": "error", "error": str(e),
                              "finished_at": _now_iso()})
    finally:
        delete_cached(_PRUNE_LOCK_KEY)
        delete_cached(_CONTACTS_CACHE_KEY)


def get_prune_status() -> Tuple[Message, int]:
    status = get_cached(_PRUNE_STATUS_KEY)
    if not status:
        msg = Message("No prune recorded")
        msg.status = {"state": "none"}
        return msg, 200
    if status.get("state") == "running":
        try:
            updated = datetime.fromisoformat(status["updated_at"])
            if (datetime.now(timezone.utc) - updated).total_seconds() > _SYNC_STALL_SECONDS:
                status = dict(status)
                status["state"] = "stalled"
        except (KeyError, ValueError):
            pass
    msg = Message("ok")
    msg.status = status
    return msg, 200


# ---------------------------------------------------------------------------
# Segment sync (background thread + redis status/lock)
# ---------------------------------------------------------------------------

def start_segment_sync(payload: dict, actor: Optional[dict]) -> Tuple[Message, int]:
    """Resolve the segment, collect contacts inline (fast), then hand the
    Resend writes to a daemon thread. Never sync inline: thousands of contact
    creates at ~20/s would blow the 120s gunicorn timeout."""
    payload = payload or {}
    segment_id = (payload.get("segment_id") or "").strip()
    segment_name = (payload.get("segment_name") or "").strip()
    if not segment_id and not segment_name:
        return Message("segment_id or segment_name is required"), 400

    resend.api_key = _resend_full_key()
    if not segment_id:
        segment_id = _get_or_create_segment(segment_name)
    if not segment_id:
        return Message("Could not resolve Resend segment"), 502

    lock_key = _sync_lock_key(segment_id)
    if get_cached(lock_key) is not None:
        msg = Message("A sync for this segment is already running")
        msg.status = "already_running"
        msg.segment_id = segment_id
        return msg, 409

    try:
        contacts, stats = collect_contacts(payload.get("sources"), payload.get("custom_emails"))
    except ValueError as e:
        return Message(str(e)), 400
    if not contacts:
        return Message("No contacts collected from the requested sources"), 400

    set_cached(lock_key, True, ttl=_SYNC_LOCK_TTL)
    status = {
        "state": "running",
        "segment_id": segment_id,
        "collected": len(contacts),
        "already_in_segment": None,
        "to_add": None,
        "added": 0,
        "failed": 0,
        "stats": stats,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "finished_at": None,
        "error": None,
        "requested_by": (actor or {}).get("email"),
        "simulated": _notifications_disabled(),
    }
    set_cached(_sync_status_key(segment_id), status, ttl=_SYNC_STATUS_TTL)

    threading.Thread(
        target=_run_segment_sync,
        args=(segment_id, contacts, actor),
        daemon=True,
    ).start()

    msg = Message("Segment sync started")
    msg.status = "started"
    msg.segment_id = segment_id
    msg.collected = len(contacts)
    msg.stats = stats
    return msg, 202


def _update_sync_status(segment_id: str, patch: dict) -> dict:
    status = get_cached(_sync_status_key(segment_id)) or {}
    status.update(patch)
    status["updated_at"] = _now_iso()
    set_cached(_sync_status_key(segment_id), status, ttl=_SYNC_STATUS_TTL)
    return status


def _run_segment_sync(segment_id: str, contacts: Dict[str, dict], actor: Optional[dict]) -> None:
    try:
        resend.api_key = _resend_full_key()
        already = _existing_segment_emails(segment_id)
        to_add = [rec for email, rec in sorted(contacts.items()) if email not in already]
        _update_sync_status(segment_id, {
            "already_in_segment": len(already),
            "to_add": len(to_add),
            "projected_total": len(already) + len(to_add),
            "over_limit": (len(already) + len(to_add)) > _contact_limit(),
        })

        added, failed = 0, 0
        if _notifications_disabled():
            _update_sync_status(segment_id, {"state": "done", "finished_at": _now_iso(),
                                             "simulated": True})
            return

        for rec in to_add:
            try:
                resend.api_key = _resend_full_key()
                # Create-only: existing contacts are never updated, so a
                # previously-unsubscribed person is never resubscribed.
                resend.Contacts.create({
                    "audience_id": segment_id,
                    "email": rec["email"],
                    "first_name": rec["first_name"],
                    "last_name": rec["last_name"],
                    "unsubscribed": False,
                })
                added += 1
            except Exception as e:
                failed += 1
                logger.warning("segment sync: failed to add %s: %s", rec["email"], e)
            if (added + failed) % 25 == 0:
                _update_sync_status(segment_id, {"added": added, "failed": failed})
            time.sleep(_CONTACT_WRITE_SLEEP)

        _update_sync_status(segment_id, {
            "state": "done",
            "added": added,
            "failed": failed,
            "finished_at": _now_iso(),
        })
        send_slack_audit(
            action="broadcast_segment_sync",
            message=f"Resend segment sync finished: segment={segment_id} "
                    f"collected={len(contacts)} added={added} failed={failed}",
            payload={"requested_by": (actor or {}).get("email")},
        )
    except Exception as e:
        logger.error("segment sync failed for %s: %s", segment_id, e)
        _update_sync_status(segment_id, {
            "state": "error",
            "error": str(e),
            "finished_at": _now_iso(),
        })
    finally:
        delete_cached(_sync_lock_key(segment_id))
        delete_cached(_CONTACTS_CACHE_KEY)  # sync changes the global contact count


def get_sync_status(segment_id: str) -> Tuple[Message, int]:
    status = get_cached(_sync_status_key(segment_id))
    if not status:
        msg = Message("No sync recorded for this segment")
        msg.status = {"state": "none", "segment_id": segment_id}
        return msg, 200

    # A daemon thread dies with its gunicorn worker; the lock TTL self-heals,
    # and a stale heartbeat is surfaced so the UI can offer a (safe,
    # idempotent) retry.
    if status.get("state") == "running":
        try:
            updated = datetime.fromisoformat(status["updated_at"])
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            if age > _SYNC_STALL_SECONDS:
                status = dict(status)
                status["state"] = "stalled"
        except (KeyError, ValueError):
            pass

    msg = Message("ok")
    msg.status = status
    return msg, 200


# ---------------------------------------------------------------------------
# Email HTML rendering (shell adapted from volunteers_service._send_email_to_user)
# ---------------------------------------------------------------------------

_EMAIL_FOOTER_HTML = """
    <!-- Donation Call-to-Action (Compact) -->
    <div style="background-color: #e8f5e8; padding: 16px; margin: 20px 0; border-radius: 6px; border-left: 3px solid #27ae60; text-align: center;">
        <h4 style="color: #27ae60; margin: 0 0 8px 0; font-size: 16px;">💚 Support Our Mission</h4>
        <p style="margin: 0 0 12px 0; color: #34495e; font-size: 14px;">Just <strong>$17 feeds a hacker</strong> building solutions for nonprofits!</p>
        <div style="margin: 12px 0;">
            <a href="https://givebutter.com/a5MSes" style="background-color: #27ae60; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 14px; margin: 0 4px;">💳 Donate Now</a>
            <a href="http://venmo.com/opportunityhack" style="color: #3D95CE; text-decoration: none; font-size: 13px; margin: 0 4px;">Venmo</a>
            <a href="http://paypal.me/opportunityhack" style="color: #0070ba; text-decoration: none; font-size: 13px; margin: 0 4px;">PayPal</a>
        </div>
        <p style="font-size: 11px; color: #666; margin: 8px 0 0 0;">Corporate employees: Find us on Benevity • 501(c)(3) tax-deductible</p>
    </div>

    <!-- Social Media Footer (Compact) -->
    <div style="background-color: #f8f9fa; padding: 16px; margin: 20px 0; border-radius: 6px; text-align: center;">
        <h4 style="color: #2c3e50; margin: 0 0 12px 0; font-size: 15px;">🌟 Stay Connected</h4>
        <div style="margin: 8px 0;">
            <a href="https://www.instagram.com/opportunityhack/" style="text-decoration: none; margin: 0 6px; color: #E4405F; font-size: 13px;">Instagram</a> |
            <a href="https://www.linkedin.com/company/opportunity-hack/" style="text-decoration: none; margin: 0 6px; color: #0A66C2; font-size: 13px;">LinkedIn</a> |
            <a href="https://slack.ohack.dev" style="text-decoration: none; margin: 0 6px; color: #4A154B; font-size: 13px;">Slack</a> |
            <a href="https://github.com/opportunity-hack/" style="text-decoration: none; margin: 0 6px; color: #333; font-size: 13px;">GitHub</a> |
            <a href="https://www.threads.net/@opportunityhack" style="text-decoration: none; margin: 0 6px; color: #000; font-size: 13px;">Threads</a> |
            <a href="https://www.facebook.com/opportunityhack" style="text-decoration: none; margin: 0 6px; color: #1877F2; font-size: 13px;">Facebook</a>
        </div>
        <p style="font-size: 11px; color: #666; margin: 8px 0 0 0;">Help us reach more people - share our mission! 🚀</p>
    </div>
"""

_UNSUBSCRIBE_FOOTER = f"""
    <hr>
    <p style="font-size: 12px; color: #666; text-align: center;">
        You are receiving this because you are part of the Opportunity Hack community.
        <a href="{UNSUBSCRIBE_PLACEHOLDER}" style="color: #666;">Unsubscribe</a>
    </p>
"""

_GREETING_MAP = {
    "mentor": "Dear Mentor",
    "sponsor": "Dear Sponsor",
    "judge": "Dear Judge",
    "hacker": "Dear Participant",
    "volunteer": "Dear Volunteer",
    "community": "Hello",
}


def _markdown_to_html(message: str) -> str:
    import html as html_lib
    try:
        return markdown.markdown(message, extensions=["nl2br", "fenced_code"])
    except Exception as markdown_error:
        logger.warning("Failed to convert markdown, falling back to basic formatting: %s",
                       markdown_error)
        return html_lib.escape(message).replace("\n", "<br>")


def render_message_html(message: str, name: str, recipient_type: str) -> str:
    """Per-recipient shell for batch sends — mirrors _send_email_to_user's look."""
    import html as html_lib
    greeting = _GREETING_MAP.get((recipient_type or "").lower(), "Hello")
    formatted_message = _markdown_to_html(message)
    return f"""
        <h2>{greeting} {html_lib.escape(name or '')},</h2>
        <p>You have received a message from the Opportunity Hack team:</p>
        <div style="background-color: #f5f5f5; padding: 15px; border-left: 4px solid #007bff; margin: 15px 0; font-family: Arial, sans-serif;">
            <p style="white-space: pre-wrap; margin: 0;">{formatted_message}</p>
        </div>
        <p>Best regards,<br>The Opportunity Hack Team</p>
        {_EMAIL_FOOTER_HTML}
        """


def render_broadcast_html(body_markdown: str) -> str:
    """Broadcast shell — no per-recipient greeting; unsubscribe link is
    mandatory (Resend rejects broadcasts without it)."""
    html = f"""
        {_markdown_to_html(body_markdown)}
        <p>Best regards,<br>The Opportunity Hack Team</p>
        {_EMAIL_FOOTER_HTML}
        """
    if UNSUBSCRIBE_PLACEHOLDER not in html:
        html += _UNSUBSCRIBE_FOOTER
    return html


# ---------------------------------------------------------------------------
# Broadcasts
# ---------------------------------------------------------------------------

def _broadcast_from_domains() -> List[str]:
    raw = os.environ.get("RESEND_BROADCAST_FROM_DOMAINS", DEFAULT_BROADCAST_FROM_DOMAINS)
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def _from_address_domain(address: str) -> Optional[str]:
    match = re.search(r"@([A-Za-z0-9.-]+)>?\s*$", address or "")
    return match.group(1).lower() if match else None


def _resolve_from_address(requested: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Returns (from_address, error). Requested addresses must be on a
    verified domain from the allowlist (notifs.ohack.org is deliberately NOT
    allowlisted — its Resend verification is partially failed)."""
    from_address = (requested or "").strip() or os.environ.get(
        "RESEND_BROADCAST_FROM", DEFAULT_BROADCAST_FROM)
    domain = _from_address_domain(from_address)
    if not domain:
        return None, f"Could not parse a domain from from_address: {from_address}"
    if domain not in _broadcast_from_domains():
        return None, (f"from_address domain '{domain}' is not in the allowed list "
                      f"({', '.join(_broadcast_from_domains())})")
    return from_address, None


def _broadcasts_api():
    api = getattr(resend, "Broadcasts", None)
    if api is None:
        raise RuntimeError(
            "The installed resend SDK has no Broadcasts API — upgrade to the "
            "version pinned in requirements.txt (pip install -U resend)"
        )
    return api


def _serialize_broadcast(b) -> dict:
    return {
        "id": _field(b, "id"),
        "name": _field(b, "name"),
        "subject": _field(b, "subject"),
        "status": _field(b, "status"),
        "segment_id": _field(b, "segment_id") or _field(b, "audience_id"),
        "created_at": _field(b, "created_at"),
        "scheduled_at": _field(b, "scheduled_at"),
        "sent_at": _field(b, "sent_at"),
    }


def create_broadcast(payload: dict, actor: Optional[dict]) -> Tuple[Message, int]:
    payload = payload or {}
    segment_id = (payload.get("segment_id") or "").strip()
    subject = (payload.get("subject") or "").strip()
    body_markdown = (payload.get("body_markdown") or "").strip()
    if not segment_id:
        return Message("segment_id is required"), 400
    if not subject:
        return Message("subject is required"), 400
    if not body_markdown:
        return Message("body_markdown is required"), 400

    from_address, from_error = _resolve_from_address(payload.get("from_address"))
    if from_error:
        return Message(from_error), 400

    html = render_broadcast_html(body_markdown)
    send_now = bool(payload.get("send"))
    scheduled_at = (payload.get("scheduled_at") or "").strip() or None

    if _notifications_disabled():
        msg = Message("Broadcast simulated (notifications disabled)")
        msg.broadcast = {"id": None, "status": "simulated", "segment_id": segment_id,
                         "subject": subject}
        msg.simulated = True
        return msg, 200

    resend.api_key = _resend_full_key()
    params = {
        "from": from_address,
        "segment_id": segment_id,
        "subject": subject,
        "html": html,
        "name": (payload.get("name") or subject)[:120],
        "reply_to": payload.get("reply_to") or DEFAULT_REPLY_TO,
    }
    if send_now:
        params["send"] = True
        if scheduled_at:
            params["scheduled_at"] = scheduled_at

    api = _broadcasts_api()
    try:
        created = api.create(params)
    except Exception as first_error:
        # Older SDK/API combos only accept the deprecated audience_id name.
        params.pop("segment_id", None)
        params["audience_id"] = segment_id
        try:
            created = api.create(params)
        except Exception:
            raise first_error

    broadcast_id = _field(created, "id")
    send_slack_audit(
        action="broadcast_created",
        message=f"Resend broadcast {'sent' if send_now else 'drafted'}: "
                f"subject='{subject}' segment={segment_id} id={broadcast_id}",
        payload={"requested_by": (actor or {}).get("email"), "from": from_address},
    )

    msg = Message("Broadcast sent" if send_now else "Broadcast draft created")
    msg.broadcast = {"id": broadcast_id, "status": "sent" if send_now else "draft",
                     "segment_id": segment_id, "subject": subject, "from": from_address}
    return msg, 201


def send_broadcast(broadcast_id: str, payload: dict, actor: Optional[dict]) -> Tuple[Message, int]:
    if not broadcast_id:
        return Message("broadcast_id is required"), 400

    if _notifications_disabled():
        msg = Message("Broadcast send simulated (notifications disabled)")
        msg.simulated = True
        return msg, 200

    resend.api_key = _resend_full_key()
    params = {"broadcast_id": broadcast_id}
    scheduled_at = ((payload or {}).get("scheduled_at") or "").strip()
    if scheduled_at:
        params["scheduled_at"] = scheduled_at
    result = _broadcasts_api().send(params)

    send_slack_audit(
        action="broadcast_sent",
        message=f"Resend broadcast sent: id={broadcast_id}",
        payload={"requested_by": (actor or {}).get("email")},
    )
    msg = Message("Broadcast sent")
    msg.broadcast = {"id": _field(result, "id") or broadcast_id}
    return msg, 200


def list_broadcasts() -> Tuple[Message, int]:
    resend.api_key = _resend_full_key()
    listed = _broadcasts_api().list()
    msg = Message("ok")
    msg.broadcasts = [_serialize_broadcast(b) for b in _resp_data(listed)]
    return msg, 200


def get_broadcast(broadcast_id: str) -> Tuple[Message, int]:
    resend.api_key = _resend_full_key()
    b = _broadcasts_api().get(broadcast_id)
    msg = Message("ok")
    msg.broadcast = _serialize_broadcast(b)
    return msg, 200


# ---------------------------------------------------------------------------
# Transactional batch send (Resend /emails/batch — 100 per call)
# ---------------------------------------------------------------------------

def batch_send_emails(payload: dict, actor: Optional[dict]) -> Tuple[Message, int]:
    """Send pre-personalized messages to email-only recipients in chunks of
    100 through the Resend Batch API (transactional quota). Recipients:
    [{email, name, message}]. Messages with [QRCode:...] markers are rejected
    per-recipient — the Batch API doesn't support attachments; the frontend
    routes those down the per-recipient path."""
    payload = payload or {}
    subject = (payload.get("subject") or "").strip()
    recipients = payload.get("recipients") or []
    recipient_type = (payload.get("recipient_type") or "community").strip()

    if not subject:
        return Message("subject is required"), 400
    if not isinstance(recipients, list) or not recipients:
        return Message("recipients is required"), 400
    if len(recipients) > MAX_BATCH_RECIPIENTS_PER_REQUEST:
        return Message(
            f"Too many recipients in one request (max {MAX_BATCH_RECIPIENTS_PER_REQUEST}); "
            "send in chunks"), 400

    email_subject = f"{subject} - Message from Opportunity Hack Team"
    results: List[dict] = []
    entries: List[Optional[dict]] = []  # params per recipient; None = pre-failed

    for r in recipients:
        r = r or {}
        email = _norm_email(r.get("email"))
        message = r.get("message") or ""
        if not email:
            results.append({"email": r.get("email"), "success": False,
                            "error": "invalid email"})
            entries.append(None)
            continue
        if QR_MARKER_RE.search(message):
            results.append({"email": email, "success": False,
                            "error": "QR-code messages are not supported by batch send — "
                                     "use the per-recipient path"})
            entries.append(None)
            continue
        results.append({"email": email, "success": False, "error": None})
        entries.append({
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "reply_to": DEFAULT_REPLY_TO,
            "subject": email_subject,
            "html": render_message_html(message, r.get("name") or email, recipient_type),
        })

    simulated = _notifications_disabled()
    if not simulated:
        resend.api_key = _resend_send_key()
        batch_api = getattr(resend, "Batch", None)

        pending = [(i, e) for i, e in enumerate(entries) if e is not None]
        for offset in range(0, len(pending), RESEND_BATCH_CHUNK):
            chunk = pending[offset:offset + RESEND_BATCH_CHUNK]
            try:
                if batch_api is not None:
                    resp = batch_api.send([e for _, e in chunk])
                    ids = [_field(item, "id") for item in _resp_data(resp)]
                    for (i, _), rid in zip(chunk, ids + [None] * len(chunk)):
                        results[i].update({"success": True, "resend_id": rid, "error": None})
                else:
                    # Dev fallback for SDKs predating Batch: sequential sends.
                    for i, entry in chunk:
                        sent = resend.Emails.send(entry)
                        results[i].update({"success": True,
                                           "resend_id": _field(sent, "id"), "error": None})
            except Exception as e:
                logger.error("batch send chunk failed: %s", e)
                for i, _ in chunk:
                    results[i].update({"success": False, "error": str(e)})
    else:
        for i, e in enumerate(entries):
            if e is not None:
                results[i].update({"success": True, "simulated": True, "error": None})

    successful = sum(1 for r in results if r["success"])
    summary = {
        "total": len(results),
        "successful": successful,
        "failed": len(results) - successful,
        "simulated": simulated,
    }
    send_slack_audit(
        action="admin_batch_email_send",
        message=f"Batch email send: subject='{subject}' "
                f"{successful}/{len(results)} successful (simulated={simulated})",
        payload={"requested_by": (actor or {}).get("email"),
                 "recipient_type": recipient_type},
    )

    msg = Message("Batch send complete")
    msg.results = results
    msg.summary = summary
    return msg, 200
