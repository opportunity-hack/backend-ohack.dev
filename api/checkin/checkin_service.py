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
