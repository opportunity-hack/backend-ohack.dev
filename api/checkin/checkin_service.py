"""
Registration-desk check-in: the narrow service behind `api/checkin`.

Why a separate path from `update_hackathon_volunteers` (the legacy admin
PATCH): that one is a general volunteer edit — any field, no event/type
check, lookup by body id only. A `checkin.admin`-only helper must be able to
mark people checked in and nothing else, so this module exposes:

- a LEAN projection of the roster / checked-in list (search + chip fields only,
  no application answers or PII beyond name/email),
- one write that touches only the check-in fields and refuses a volunteer
  from another event or type (the QR encodes `eventId|volunteerId|type`).

Import order: `common.utils.slack` is pulled in via hackathons_service before
firebase (see CLAUDE.md dotenv note).
"""
from datetime import datetime, timezone
from typing import Optional

from common.log import get_logger
from common.utils.slack import send_slack_audit
from services import hackathons_service
from services.users_service import get_propel_user_details_by_id

logger = get_logger("checkin_service")

VOLUNTEER_TYPES = ("mentor", "judge", "volunteer", "hacker", "sponsor")

# Everything the check-in workbench renders or searches on. Keep in sync with
# the frontend's Manual Entry autocomplete (name/email) and the checked-in list.
LEAN_FIELDS = (
    "id",
    "name",
    "email",
    "volunteer_type",
    "isSelected",
    "status",
    "checkedIn",
    "checkedInAt",
    "checkedInBy",
    "company",
    "companyName",
    "title",
    # Mentor self check-in (online) writes these but no `checkedInAt` — the
    # admin reset dialog uses them to tell an online check-in from a desk scan.
    "isCheckedIn",
    "checkInTime",
)

# The reset accepts at most this many explicit ids per call.
MAX_RESET_IDS = 500
# Firestore allows 500 writes per batch; stay well under it.
_RESET_BATCH_SIZE = 400

# Every field a self check-in (mentor_checkin / mentor_checkout) or a desk
# scan (check_in_volunteer) writes. A reset sets explicit values — no
# DELETE_FIELD — so MockFirestore behaves like prod and every reader that
# already tolerates null/false keeps working.
_RESET_VALUES = {
    "checkedIn": False,
    "isCheckedIn": False,
    "checkInTime": None,
    "checkedInAt": None,
    "checkedInBy": None,
    "timeSlot": None,
    "checkOutTime": None,
    "checkInDuration": None,
    "checkInTimeList": [],
    "checkoutTimeList": [],
}
# What the audit entry remembers about the cleared state.
_RESET_HISTORY_KEYS = (
    "checkedIn", "isCheckedIn", "checkInTime", "checkedInAt",
    "timeSlot", "checkInTimeList", "checkoutTimeList",
)


class CheckInError(Exception):
    def __init__(self, status, error, message):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


def _get_db():
    from db.db import get_db

    return get_db()


def is_valid_type(volunteer_type: Optional[str]) -> bool:
    return (volunteer_type or "").lower() in VOLUNTEER_TYPES


def lean_volunteer(doc: dict) -> dict:
    """Project a volunteer doc to the check-in allowlist (missing keys skipped)."""
    out = {k: doc[k] for k in LEAN_FIELDS if k in doc}
    # Every row carries a boolean so the UI never reads a missing key.
    out["checkedIn"] = doc.get("checkedIn") is True
    return out


def _rows(payload) -> list:
    if isinstance(payload, dict):
        return payload.get("data") or []
    return payload or []


def get_checkin_roster(event_id: str, volunteer_type: str) -> list:
    """Lean roster for one volunteer type (admin view, so unapproved rows are
    included — hackers can apply at the desk and be checked in)."""
    payload = hackathons_service.get_volunteer_by_event(event_id, volunteer_type, admin=True)
    return [lean_volunteer(v) for v in _rows(payload) if isinstance(v, dict)]


def get_checked_in(event_id: str, volunteer_type: str) -> list:
    payload = hackathons_service.get_volunteer_checked_in_by_event(event_id, volunteer_type)
    return [lean_volunteer(v) for v in _rows(payload) if isinstance(v, dict)]


def _clear_caches():
    hackathons_service.get_volunteer_by_event.cache_clear()
    hackathons_service.get_volunteer_checked_in_by_event.cache_clear()


def check_in_volunteer(event_id: str, volunteer_type: str, volunteer_id: str, propel_id: str) -> dict:
    """Mark ONE volunteer checked in. Writes only the check-in fields.

    Raises CheckInError(404) when the doc is missing and CheckInError(409,
    "wrong_event") when the doc belongs to another event or type. A repeat
    check-in is idempotent: the original timestamp is kept and no Slack
    welcome is sent again.
    """
    volunteer_type = (volunteer_type or "").lower()
    db = _get_db()
    doc_ref = db.collection("volunteers").document(volunteer_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise CheckInError(404, "not_found", "No volunteer with that id.")
    doc = snap.to_dict() or {}
    doc["id"] = volunteer_id

    if doc.get("event_id") != event_id or (doc.get("volunteer_type") or "").lower() != volunteer_type:
        raise CheckInError(
            409,
            "wrong_event",
            "This code belongs to a different event or role.",
        )

    already = hackathons_service.volunteer_was_checked_in(doc)
    if already:
        logger.info(f"check_in_volunteer: {volunteer_id} already checked in ({doc.get('checkedInAt')})")
        return {"data": lean_volunteer(doc), "already_checked_in": True}

    admin_email, _uid, _ll, _img, admin_name, _nick = get_propel_user_details_by_id(propel_id)
    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "checkedIn": True,
        "checkedInAt": now,
        "checkedInBy": {
            "userId": propel_id,
            "email": admin_email,
            "name": admin_name,
        },
        "updated_by": admin_name or admin_email or propel_id,
        "updated_timestamp": now,
    }
    doc_ref.update(patch)
    send_slack_audit(
        action="check_in_volunteer",
        message=f"Checked in {volunteer_type} {volunteer_id} for {event_id}",
        payload={"volunteer_id": volunteer_id, "event_id": event_id, "by": admin_email},
    )
    hackathons_service.send_checkin_welcome(
        doc, event_id, volunteer_id, checking_in=True, was_checked_in=False
    )
    _clear_caches()

    doc.update(patch)
    return {"data": lean_volunteer(doc), "already_checked_in": False}


def _was_any_checked_in(doc: dict) -> bool:
    """Desk scan (`checkedIn`), mentor self check-in (`isCheckedIn`) or a
    leftover `checkInTime` all count — a reset clears whichever is set."""
    return (
        doc.get("checkedIn") is True
        or doc.get("isCheckedIn") is True
        or bool(doc.get("checkInTime"))
    )


def _clear_person_caches(doc: dict, event_id: str, volunteer_type: str):
    """Per-person redis caches behind /api/mentor/application and
    /api/mentor/me/events. Best-effort; a stale 2s cache is not fatal."""
    try:
        from services.volunteers_service import _clear_volunteer_caches
    except Exception as e:  # pragma: no cover - import guard for slim test envs
        logger.warning(f"reset_checkins: could not import cache helpers: {e}")
        return
    try:
        _clear_volunteer_caches(doc.get("user_id"), doc.get("email"), event_id, volunteer_type)
    except Exception as e:
        logger.warning(f"reset_checkins: cache clear failed for {doc.get('id')}: {e}")


def _commit_updates(db, updates):
    """Apply [(ref, patch)] in Firestore batches; MockFirestore has no
    batch(), so fall back to one update per doc there."""
    batch_factory = getattr(db, "batch", None)
    if not callable(batch_factory):
        for ref, patch in updates:
            ref.update(patch)
        return
    for start in range(0, len(updates), _RESET_BATCH_SIZE):
        batch = batch_factory()
        for ref, patch in updates[start:start + _RESET_BATCH_SIZE]:
            batch.update(ref, patch)
        batch.commit()


def reset_checkins(event_id: str, volunteer_type: str, propel_id: str, volunteer_ids=None) -> dict:
    """Clear check-ins for one event + role (admin only — see checkin_views).

    `volunteer_ids=None` resets everyone of that type on the event who is
    checked in; a list resets just those docs. Docs from another event/type
    are skipped (`wrong_event`), missing ones `not_found`, and people who are
    not checked in `not_checked_in` (so a second reset is a no-op).

    Each reset doc gets an entry appended to `checkin_reset_history` (what was
    cleared, by whom). ONE Slack audit message; nobody else is notified.
    """
    from google.cloud import firestore

    volunteer_type = (volunteer_type or "").lower()
    db = _get_db()
    collection = db.collection("volunteers")

    skipped = []
    candidates = []  # (ref, doc)
    if volunteer_ids is None:
        query = collection.where("event_id", "==", event_id).where("volunteer_type", "==", volunteer_type)
        for snap in query.stream():
            doc = snap.to_dict() or {}
            doc["id"] = snap.id
            if _was_any_checked_in(doc):
                candidates.append((collection.document(snap.id), doc))
    else:
        seen = set()
        ids = [i for i in volunteer_ids if isinstance(i, str) and i and not (i in seen or seen.add(i))]
        refs = [collection.document(i) for i in ids]
        snaps = list(db.get_all(refs)) if refs else []
        by_id = {snap.id: snap for snap in snaps}
        for vid, ref in zip(ids, refs):
            snap = by_id.get(vid)
            if snap is None or not snap.exists:
                skipped.append({"id": vid, "reason": "not_found"})
                continue
            doc = snap.to_dict() or {}
            doc["id"] = vid
            if doc.get("event_id") != event_id or (doc.get("volunteer_type") or "").lower() != volunteer_type:
                skipped.append({"id": vid, "reason": "wrong_event"})
                continue
            if not _was_any_checked_in(doc):
                skipped.append({"id": vid, "reason": "not_checked_in"})
                continue
            candidates.append((ref, doc))

    if not candidates:
        return {"reset": 0, "ids": [], "skipped": skipped}

    admin_email = admin_name = None
    try:
        details = get_propel_user_details_by_id(propel_id) or ()
        admin_email = details[0] if len(details) > 0 else None
        admin_name = details[4] if len(details) > 4 else None
    except Exception as e:
        logger.warning(f"reset_checkins: could not resolve admin {propel_id}: {e}")
    by = admin_name or admin_email or propel_id
    now = datetime.now(timezone.utc).isoformat()

    updates = []
    for ref, doc in candidates:
        patch = dict(_RESET_VALUES)
        patch["checkin_reset_history"] = firestore.ArrayUnion([{
            "at": now,
            "by": by,
            "by_email": admin_email,
            "previous": {k: doc.get(k) for k in _RESET_HISTORY_KEYS},
        }])
        patch["updated_by"] = by
        patch["updated_timestamp"] = now
        updates.append((ref, patch))
    _commit_updates(db, updates)

    reset_ids = [doc["id"] for _ref, doc in candidates]
    _clear_caches()
    for _ref, doc in candidates:
        _clear_person_caches(doc, event_id, volunteer_type)

    send_slack_audit(
        action="reset_checkins",
        message=f"Reset {len(reset_ids)} {volunteer_type} check-in(s) for {event_id}",
        payload={
            "event_id": event_id,
            "volunteer_type": volunteer_type,
            "count": len(reset_ids),
            "scope": "all" if volunteer_ids is None else "ids",
            "by": admin_email or by,
        },
    )
    return {"reset": len(reset_ids), "ids": reset_ids, "skipped": skipped}
