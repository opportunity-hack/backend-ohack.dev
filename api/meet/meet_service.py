"""Meeting scheduling polls ("find a time", Doodle-style).

An admin creates a poll with a short code and a set of candidate start
times; anyone holding ``/meet?code=<code>`` marks each time yes / maybe /
no and sees everyone else's answers. When the votes are in, an admin picks
the final time and the meeting is created on the @ohack.org Google Calendar
with a Meet link — Google emails the invitations.

Collections:
- ``meet_polls`` (doc id = code, lowercase): title, description,
  duration_minutes, slots ``[{id, start}]`` (UTC ``YYYY-MM-DDTHH:MM:SSZ``),
  status ``open|closed|finalized``, final_slot_id, calendar_event_id,
  meet_link, calendar_html_link, invited_emails, created_*/updated_at.
- ``meet_votes`` (doc id = sha256(code|email)[:24]): poll_code, name, email,
  choices ``{slot_id: yes|maybe|no}``, edit_token_hash, created_at,
  updated_at. Top-level (not a subcollection) so a single-field
  ``where("poll_code")`` lists a poll's votes with no composite index.

Rules:
- All times are stored in UTC; the browser renders them in the viewer's
  timezone. Nothing here knows a respondent's timezone.
- The public view never contains an email, the Meet link or the edit-token
  hash. Respondent names and answers are public to anyone with the code,
  like Doodle. The Meet link reaches people only through their invitation.
- One response per email per poll. The first response returns an
  ``editToken`` the browser keeps; changing that response needs it (409
  ``already_responded`` otherwise) so a stranger cannot overwrite someone's
  answers by typing their email. An admin can delete a response.
- Public writes are reCAPTCHA-gated (same check as the contact form) and
  capped at ``MAX_VOTES_PER_POLL``.
- Finalizing creates the calendar event FIRST (deterministic event id, so a
  retry reuses it) and only then marks the poll finalized; a calendar
  failure leaves the poll open. ``sendInvites: false`` records the chosen
  time without touching Google Calendar.
"""
import hashlib
import hmac
import html
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from db.db import get_db
from common.log import get_logger
from common.utils import google_calendar

logger = get_logger(__name__)

POLLS = "meet_polls"
VOTES = "meet_votes"

MAX_SLOTS = 40
MAX_VOTES_PER_POLL = 200
MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
MAX_NAME_LENGTH = 80
MAX_EMAIL_LENGTH = 254
MAX_EXTRA_EMAILS = 50
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 480
DEFAULT_DURATION_MINUTES = 30

CHOICES = ("yes", "maybe", "no")
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_FINALIZED = "finalized"

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,39}$")
RESERVED_CODES = {"admin", "polls", "votes", "new"}
# No 0/o/1/l/i: codes get read aloud and typed from a slide.
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Response notifications: every new response, and an edit at most once per
# respondent per interval (the page autosaves each change).
DEFAULT_NOTIFY_SLACK_CHANNEL = "meeting-updates"
DEFAULT_NOTIFY_EMAIL = "meet@ohack.org"
EDIT_NOTIFY_INTERVAL_SECONDS = 15 * 60
NOTIFY_FROM = "Opportunity Hack <welcome@notifs.ohack.org>"
MEET_SITE_URL = "https://www.ohack.dev"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(code: str, message: str, status: int = 400) -> Tuple[Dict[str, Any], int]:
    return {"success": False, "error": code, "message": message}, status


def verify_recaptcha(token: Optional[str]) -> bool:
    """Verify a reCAPTCHA token with Google's API (same as contact form)."""
    recaptcha_key = os.environ.get("GOOGLE_CAPTCHA_SECRET_KEY")
    if not recaptcha_key:
        logger.warning("No GOOGLE_CAPTCHA_SECRET_KEY set, skipping verification")
        return True
    try:
        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": recaptcha_key, "response": token},
            timeout=10,
        )
        return response.json().get("success", False)
    except Exception as e:
        logger.exception("Error verifying recaptcha: %s", str(e))
        return False


def _audit(action: str, message: str) -> None:
    """Best-effort Slack audit line. Never raises; silent under
    ENVIRONMENT=test. No respondent emails in the text."""
    if os.environ.get("ENVIRONMENT") == "test":
        return
    try:
        from common.utils.slack import send_slack_audit

        send_slack_audit(action=action, message=" ".join(message.split()), payload={})
    except Exception as e:
        logger.warning("Meet audit failed (%s): %s", action, type(e).__name__)


# ---------------------------------------------------------------------------
# Response notifications (Slack channel + team inbox)
# ---------------------------------------------------------------------------

def _notifications_disabled() -> bool:
    return os.environ.get("ENVIRONMENT") == "test"


def _notify_slack_channel() -> str:
    """Empty env value turns the Slack post off."""
    return os.environ.get("MEET_NOTIFY_SLACK_CHANNEL", DEFAULT_NOTIFY_SLACK_CHANNEL).strip()


def _notify_email() -> str:
    """Empty env value turns the email off."""
    return os.environ.get("MEET_NOTIFY_EMAIL", DEFAULT_NOTIFY_EMAIL).strip()


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slack_escape(value: Any) -> str:
    """Names come from an anonymous public form: without this a name could
    carry ``<!channel>`` or a link into the channel."""
    return (_one_line(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _edit_notify_due(vote: Dict[str, Any], now_iso: str) -> bool:
    last = vote.get("last_notified_at")
    if not last:
        return True
    try:
        elapsed = datetime.fromisoformat(now_iso) - datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    return elapsed.total_seconds() >= EDIT_NOTIFY_INTERVAL_SECONDS


def build_response_notification(
    poll: Dict[str, Any], name: str, choices: Dict[str, str], is_update: bool,
    response_count: Optional[int] = None,
) -> Dict[str, str]:
    """Pure: the Slack text and the email subject/HTML for one response.
    Name only, never the respondent's email."""
    slot_ids = [s["id"] for s in poll.get("slots") or []]
    yes = sum(1 for sid in slot_ids if choices.get(sid) == "yes")
    maybe = sum(1 for sid in slot_ids if choices.get(sid) == "maybe")
    summary = "yes to %d of %d times" % (yes, len(slot_ids))
    if maybe:
        summary += ", maybe to %d" % maybe
    if response_count is not None:
        summary += " · %d response%s so far" % (
            response_count, "" if response_count == 1 else "s")
    verb = "updated their times for" if is_update else "responded to"
    code = poll.get("code") or ""
    title = _one_line(poll.get("title")) or code
    who = _one_line(name)
    poll_url = "%s/meet?code=%s" % (MEET_SITE_URL, code)
    admin_url = "%s/admin/meet" % MEET_SITE_URL

    slack = ":calendar: *%s* %s *%s* — %s\n<%s|Open poll> · <%s|Admin>" % (
        _slack_escape(who), verb, _slack_escape(title), summary, poll_url, admin_url)
    body = (
        '<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">'
        '<p style="font-size: 16px;"><strong>%s</strong> %s <strong>%s</strong>.</p>'
        '<p style="font-size: 14px; color: #555;">%s</p>'
        '<p style="font-size: 14px;"><a href="%s" style="color: #3498db;">Open the poll</a>'
        ' · <a href="%s" style="color: #3498db;">Admin</a></p>'
        '<p style="font-size: 12px; color: #777;">Later changes by the same person are '
        'announced at most once every %d minutes; the poll page always shows the '
        'current answers.</p></div>'
    ) % (html.escape(who), verb, html.escape(title), html.escape(summary),
         poll_url, admin_url, EDIT_NOTIFY_INTERVAL_SECONDS // 60)
    return {"slack": slack, "subject": "[meet] %s %s %s" % (who, verb, title),
            "html": body}


def _send_response_notifications(
    poll: Dict[str, Any], name: str, choices: Dict[str, str], is_update: bool,
) -> None:
    """Runs on the background pool. Each channel fails on its own."""
    code = poll.get("code")
    try:
        count: Optional[int] = len(_load_votes(get_db(), code))
    except Exception as e:
        logger.warning("Meet notify: response count failed: %s", type(e).__name__)
        count = None
    note = build_response_notification(poll, name, choices, is_update, count)

    channel = _notify_slack_channel()
    if channel:
        try:
            from common.utils.slack import send_slack

            send_slack(message=note["slack"], channel=channel,
                       unfurl_links=False, unfurl_media=False)
        except Exception as e:
            logger.warning("Meet notify: Slack failed for %s: %s", code, type(e).__name__)

    to_email = _notify_email()
    if to_email:
        try:
            import resend
            from services.resend_utils import send_with_retry

            key = os.environ.get("RESEND_WELCOME_EMAIL_KEY")
            if not key:
                logger.warning("Meet notify: RESEND_WELCOME_EMAIL_KEY not set, no email")
                return
            resend.api_key = key
            params = {"from": NOTIFY_FROM, "to": [to_email],
                      "subject": note["subject"], "html": note["html"]}
            _, error = send_with_retry(lambda: resend.Emails.send(params),
                                       log_context={"meet_poll": code})
            if error:
                logger.warning("Meet notify: email not sent for %s: %s", code, error)
        except Exception as e:
            logger.warning("Meet notify: email failed for %s: %s", code, type(e).__name__)


def _notify_response(
    poll: Dict[str, Any], name: str, choices: Dict[str, str], is_update: bool,
) -> None:
    """Best-effort and off the request thread: never raises, never delays
    the respondent. Silent under ENVIRONMENT=test."""
    try:
        if _notifications_disabled():
            return
        from common.utils.slack import run_in_background

        run_in_background("meet_response_notify", _send_response_notifications,
                          dict(poll), name, dict(choices), is_update)
    except Exception as e:
        logger.warning("Meet notify failed to start: %s", type(e).__name__)


# ---------------------------------------------------------------------------
# Validation helpers (pure)
# ---------------------------------------------------------------------------

def normalize_code(value: Any) -> Optional[str]:
    """Lowercased code, or None when it isn't a usable one."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    if not _CODE_RE.match(code) or code in RESERVED_CODES:
        return None
    return code


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def normalize_start(value: Any) -> Optional[str]:
    """ISO datetime WITH an offset (or Z) -> ``YYYY-MM-DDTHH:MM:SSZ``. A
    naive value is refused: the server cannot know whose clock it is."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _parse_utc(start: str) -> datetime:
    return datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def normalize_slots(
    raw: Any, existing: Optional[List[Dict[str, Any]]] = None
) -> Tuple[Optional[List[Dict[str, str]]], Optional[str]]:
    """Validate the slot list. A slot keeps its id when the payload carries
    one the poll already has (so votes survive an edit of the time); every
    other slot gets a new id. Duplicate start times collapse. Sorted by
    start. Returns (slots, error_message)."""
    if not isinstance(raw, list) or not raw:
        return None, "Add at least one time"
    if len(raw) > MAX_SLOTS:
        return None, "A poll can have at most %d times" % MAX_SLOTS
    known_ids = {s.get("id") for s in (existing or [])}
    slots: List[Dict[str, str]] = []
    seen_starts = set()
    seen_ids = set()
    for item in raw:
        if isinstance(item, str):
            item = {"start": item}
        if not isinstance(item, dict):
            return None, "Each time must be an object with a start"
        start = normalize_start(item.get("start"))
        if not start:
            return None, "Each time needs a start with a timezone offset"
        if start in seen_starts:
            continue
        seen_starts.add(start)
        slot_id = item.get("id")
        if not isinstance(slot_id, str) or slot_id not in known_ids or slot_id in seen_ids:
            slot_id = uuid.uuid4().hex[:10]
        seen_ids.add(slot_id)
        slots.append({"id": slot_id, "start": start})
    slots.sort(key=lambda s: s["start"])
    return slots, None


def _clean_text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if len(text) <= limit else None


def _clean_duration(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    if minutes < MIN_DURATION_MINUTES or minutes > MAX_DURATION_MINUTES:
        return None
    return minutes


def _clean_email(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(email):
        return None
    return email


def vote_id_for(code: str, email: str) -> str:
    return hashlib.sha256(("%s|%s" % (code, email)).encode("utf-8")).hexdigest()[:24]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def calendar_event_id(code: str, slot_id: str) -> str:
    """Deterministic Google Calendar event id (base32hex alphabet: hex
    digits qualify) so finalizing the same slot twice yields ONE event."""
    return "ohackmeet" + hashlib.sha1(
        ("%s|%s" % (code, slot_id)).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Views of a poll / vote
# ---------------------------------------------------------------------------

def _public_poll(poll: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": poll.get("code"),
        "title": poll.get("title") or "",
        "description": poll.get("description") or "",
        "duration_minutes": poll.get("duration_minutes") or DEFAULT_DURATION_MINUTES,
        "slots": poll.get("slots") or [],
        "status": poll.get("status") or STATUS_OPEN,
        "final_slot_id": poll.get("final_slot_id"),
        "invites_sent": bool(poll.get("calendar_event_id")),
    }


def _admin_poll(poll: Dict[str, Any]) -> Dict[str, Any]:
    view = _public_poll(poll)
    for key in (
        "created_at", "updated_at", "created_by_name", "finalized_at",
        "finalized_by_name", "meet_link", "calendar_html_link",
        "calendar_event_id", "invited_emails",
    ):
        view[key] = poll.get(key)
    return view


def _public_vote(vote: Dict[str, Any], slot_ids: List[str]) -> Dict[str, Any]:
    choices = vote.get("choices") or {}
    return {
        "id": vote.get("id"),
        "name": vote.get("name") or "",
        "choices": {sid: choices.get(sid, "no") for sid in slot_ids},
        "updated_at": vote.get("updated_at"),
    }


def _admin_vote(vote: Dict[str, Any], slot_ids: List[str]) -> Dict[str, Any]:
    view = _public_vote(vote, slot_ids)
    view["email"] = vote.get("email") or ""
    view["created_at"] = vote.get("created_at")
    return view


def _load_poll(db: Any, code: Optional[str]) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    snap = db.collection(POLLS).document(code).get()
    if snap is None or not snap.exists:
        return None
    poll = snap.to_dict() or {}
    poll["code"] = code
    return poll


def _load_votes(db: Any, code: str) -> List[Dict[str, Any]]:
    votes = []
    for doc in db.collection(VOTES).where("poll_code", "==", code).stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        votes.append(data)
    votes.sort(key=lambda v: v.get("created_at") or "")
    return votes


def _slot_ids(poll: Dict[str, Any]) -> List[str]:
    return [s["id"] for s in poll.get("slots") or []]


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def get_public_poll(code: Any) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "No meeting poll matches that code", 404)
    slot_ids = _slot_ids(poll)
    votes = [_public_vote(v, slot_ids) for v in _load_votes(db, poll["code"])]
    return {"poll": _public_poll(poll), "votes": votes}, 200


def submit_vote(code: Any, data: Any) -> Tuple[Dict[str, Any], int]:
    """Create or update the caller's response. Body: ``{name, email,
    choices: {slot_id: yes|maybe|no}, editToken?, recaptchaToken}``."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    if (
        not verify_recaptcha(data.get("recaptchaToken"))
        and os.environ.get("FLASK_ENV") != "development"
    ):
        logger.warning("reCAPTCHA verification failed for meet vote")
        return _err("invalid_recaptcha", "reCAPTCHA verification failed")

    name = _clean_text(data.get("name"), MAX_NAME_LENGTH)
    if not name:
        return _err("invalid_name", "Enter your name (up to %d characters)" % MAX_NAME_LENGTH)
    email = _clean_email(data.get("email"))
    if not email:
        return _err("invalid_email", "Enter a valid email so we can send you the invite")
    raw_choices = data.get("choices")
    if not isinstance(raw_choices, dict):
        return _err("invalid_choices", "Choices must be an object keyed by time")

    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "No meeting poll matches that code", 404)
    if poll.get("status") != STATUS_OPEN:
        return _err("poll_closed", "This poll is no longer taking responses", 409)

    choices: Dict[str, str] = {}
    for slot_id in _slot_ids(poll):
        answer = raw_choices.get(slot_id, "no")
        if answer not in CHOICES:
            return _err("invalid_choices", "Each answer must be yes, maybe or no")
        choices[slot_id] = answer

    vote_id = vote_id_for(poll["code"], email)
    ref = db.collection(VOTES).document(vote_id)
    snap = ref.get()
    now = _now_iso()
    edit_token = data.get("editToken")
    if snap is not None and snap.exists:
        existing = snap.to_dict() or {}
        if not isinstance(edit_token, str) or not hmac.compare_digest(
            _hash_token(edit_token), existing.get("edit_token_hash") or ""
        ):
            return _err(
                "already_responded",
                "Someone already responded with this email. Change it from the "
                "browser you first used, or ask the organizer to remove it.",
                409,
            )
        # Full set (not merge): choices is a map and merge would keep
        # answers for times that no longer exist.
        changed = (existing.get("name") != name
                   or (existing.get("choices") or {}) != choices)
        notify = changed and _edit_notify_due(existing, now)
        existing.update({"name": name, "choices": choices, "updated_at": now})
        if notify:
            existing["last_notified_at"] = now
        ref.set(existing)
        if notify:
            _notify_response(poll, name, choices, is_update=True)
        return {"success": True, "voteId": vote_id, "editToken": edit_token,
                "updated": True}, 200

    if len(_load_votes(db, poll["code"])) >= MAX_VOTES_PER_POLL:
        return _err("poll_full", "This poll has reached its response limit", 409)
    edit_token = secrets.token_urlsafe(24)
    ref.set({
        "poll_code": poll["code"],
        "name": name,
        "email": email,
        "choices": choices,
        "edit_token_hash": _hash_token(edit_token),
        "created_at": now,
        "updated_at": now,
        "last_notified_at": now,
    })
    _notify_response(poll, name, choices, is_update=False)
    return {"success": True, "voteId": vote_id, "editToken": edit_token,
            "updated": False}, 201


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def _calendar_status() -> Dict[str, Any]:
    return {
        "calendar_configured": google_calendar.is_configured(),
        "calendar_organizer": google_calendar.organizer(),
    }


def admin_list_polls() -> Tuple[Dict[str, Any], int]:
    db = get_db()
    counts: Dict[str, int] = {}
    for doc in db.collection(VOTES).stream():
        poll_code = (doc.to_dict() or {}).get("poll_code")
        counts[poll_code] = counts.get(poll_code, 0) + 1
    polls = []
    for doc in db.collection(POLLS).stream():
        poll = doc.to_dict() or {}
        poll["code"] = doc.id
        view = _admin_poll(poll)
        view["vote_count"] = counts.get(doc.id, 0)
        polls.append(view)
    polls.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    payload = {"data": polls}
    payload.update(_calendar_status())
    return payload, 200


def admin_get_poll(code: Any) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "Poll not found", 404)
    slot_ids = _slot_ids(poll)
    payload = {
        "poll": _admin_poll(poll),
        "votes": [_admin_vote(v, slot_ids) for v in _load_votes(db, poll["code"])],
    }
    payload.update(_calendar_status())
    return payload, 200


def admin_create_poll(
    data: Any, admin: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], int]:
    """Body: ``{code?, title, description?, durationMinutes?, slots}``."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    admin = admin or {}
    title = _clean_text(data.get("title"), MAX_TITLE_LENGTH)
    if not title:
        return _err("invalid_title", "Give the meeting a title (up to %d characters)" % MAX_TITLE_LENGTH)
    description = _clean_text(data.get("description"), MAX_DESCRIPTION_LENGTH)
    if description is None:
        return _err("invalid_description", "Description is too long")
    duration = _clean_duration(data.get("durationMinutes", DEFAULT_DURATION_MINUTES))
    if duration is None:
        return _err("invalid_duration", "Duration must be %d to %d minutes"
                    % (MIN_DURATION_MINUTES, MAX_DURATION_MINUTES))
    slots, slot_error = normalize_slots(data.get("slots"))
    if slot_error:
        return _err("invalid_slots", slot_error)

    db = get_db()
    raw_code = data.get("code")
    if isinstance(raw_code, str) and raw_code.strip():
        code = normalize_code(raw_code)
        if not code:
            return _err("invalid_code", "Code must be 3 to 40 letters, numbers, - or _")
        if _load_poll(db, code):
            return _err("code_taken", "A poll already uses that code", 409)
    else:
        code = generate_code()
        for _ in range(5):
            if not _load_poll(db, code):
                break
            code = generate_code()

    now = _now_iso()
    poll = {
        "code": code,
        "title": title,
        "description": description,
        "duration_minutes": duration,
        "slots": slots,
        "status": STATUS_OPEN,
        "final_slot_id": None,
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("propel_id"),
        "created_by_name": admin.get("name") or admin.get("email"),
    }
    db.collection(POLLS).document(code).set(poll)
    _audit("meet_poll_created", "Meeting poll '%s' created (/meet?code=%s, %d times)"
           % (title, code, len(slots)))
    return {"success": True, "poll": _admin_poll(poll)}, 201


def admin_update_poll(code: Any, data: Any) -> Tuple[Dict[str, Any], int]:
    """Body may carry ``title``, ``description``, ``durationMinutes``,
    ``slots`` and ``status`` (open|closed). A finalized poll is read-only."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "Poll not found", 404)
    if poll.get("status") == STATUS_FINALIZED:
        return _err("already_finalized", "This meeting is already scheduled", 409)

    updates: Dict[str, Any] = {}
    if "title" in data:
        title = _clean_text(data.get("title"), MAX_TITLE_LENGTH)
        if not title:
            return _err("invalid_title", "Give the meeting a title")
        updates["title"] = title
    if "description" in data:
        description = _clean_text(data.get("description"), MAX_DESCRIPTION_LENGTH)
        if description is None:
            return _err("invalid_description", "Description is too long")
        updates["description"] = description
    if "durationMinutes" in data:
        duration = _clean_duration(data.get("durationMinutes"))
        if duration is None:
            return _err("invalid_duration", "Duration must be %d to %d minutes"
                        % (MIN_DURATION_MINUTES, MAX_DURATION_MINUTES))
        updates["duration_minutes"] = duration
    if "slots" in data:
        slots, slot_error = normalize_slots(data.get("slots"), poll.get("slots"))
        if slot_error:
            return _err("invalid_slots", slot_error)
        updates["slots"] = slots
    if "status" in data:
        if data.get("status") not in (STATUS_OPEN, STATUS_CLOSED):
            return _err("invalid_status", "Status must be open or closed")
        updates["status"] = data["status"]
    if not updates:
        return _err("missing_fields", "Nothing to update")
    updates["updated_at"] = _now_iso()
    # update(), not set(merge=True): slots is a list and is replaced whole.
    db.collection(POLLS).document(poll["code"]).update(updates)
    poll.update(updates)
    return {"success": True, "poll": _admin_poll(poll)}, 200


def admin_delete_poll(code: Any) -> Tuple[Dict[str, Any], int]:
    """Delete the poll and its responses. Does NOT cancel a calendar event
    that was already sent — do that in Google Calendar."""
    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "Poll not found", 404)
    for vote in _load_votes(db, poll["code"]):
        db.collection(VOTES).document(vote["id"]).delete()
    db.collection(POLLS).document(poll["code"]).delete()
    _audit("meet_poll_deleted", "Meeting poll '%s' (%s) deleted"
           % (poll.get("title"), poll["code"]))
    return {"success": True}, 200


def admin_delete_vote(code: Any, vote_id: str) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    code = normalize_code(code)
    ref = db.collection(VOTES).document(vote_id)
    snap = ref.get()
    if (
        not code or snap is None or not snap.exists
        or (snap.to_dict() or {}).get("poll_code") != code
    ):
        return _err("not_found", "Response not found", 404)
    ref.delete()
    return {"success": True}, 200


def _clean_extra_emails(raw: Any) -> Tuple[Optional[List[str]], Optional[str]]:
    if raw is None:
        return [], None
    if not isinstance(raw, list) or len(raw) > MAX_EXTRA_EMAILS:
        return None, "extraEmails must be a list of up to %d addresses" % MAX_EXTRA_EMAILS
    emails = []
    for item in raw:
        email = _clean_email(item)
        if not email:
            return None, "'%s' is not a valid email" % str(item)[:80]
        emails.append(email)
    return emails, None


def admin_finalize_poll(
    code: Any, data: Any, admin: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], int]:
    """Pick the final time. Body: ``{slotId, sendInvites (bool, default
    true), voteIds? (who to invite; default every respondent),
    extraEmails?}``. With ``sendInvites`` the event is created on the
    organizer's Google Calendar with a Meet link and Google emails every
    attendee."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    admin = admin or {}
    send_invites = data.get("sendInvites", True)
    if not isinstance(send_invites, bool):
        return _err("invalid_send_invites", "sendInvites must be true or false")
    extra_emails, extra_error = _clean_extra_emails(data.get("extraEmails"))
    if extra_error:
        return _err("invalid_extra_emails", extra_error)
    vote_ids = data.get("voteIds")
    if vote_ids is not None and (
        not isinstance(vote_ids, list) or not all(isinstance(v, str) for v in vote_ids)
    ):
        return _err("invalid_vote_ids", "voteIds must be a list of response ids")

    db = get_db()
    poll = _load_poll(db, normalize_code(code))
    if not poll:
        return _err("not_found", "Poll not found", 404)
    if poll.get("status") == STATUS_FINALIZED:
        return _err("already_finalized", "This meeting is already scheduled", 409)
    slot = next((s for s in poll.get("slots") or [] if s["id"] == data.get("slotId")), None)
    if not slot:
        return _err("invalid_slot", "Pick one of the poll's times")

    attendees: List[Dict[str, str]] = []
    seen = set()
    for vote in _load_votes(db, poll["code"]):
        if vote_ids is not None and vote["id"] not in vote_ids:
            continue
        email = vote.get("email")
        if email and email not in seen:
            seen.add(email)
            attendees.append({"email": email, "name": vote.get("name") or ""})
    for email in extra_emails or []:
        if email not in seen:
            seen.add(email)
            attendees.append({"email": email, "name": ""})

    updates: Dict[str, Any] = {
        "status": STATUS_FINALIZED,
        "final_slot_id": slot["id"],
        "finalized_at": _now_iso(),
        "finalized_by": admin.get("propel_id"),
        "finalized_by_name": admin.get("name") or admin.get("email"),
    }
    if send_invites:
        if not google_calendar.is_configured():
            return _err(
                "calendar_not_configured",
                "Google Calendar is not connected. Set GOOGLE_CALENDAR_SA_CONFIG "
                "and GOOGLE_CALENDAR_ORGANIZER, or finalize without sending invites.",
                503,
            )
        if not attendees:
            return _err("no_attendees", "There is nobody to invite yet")
        start = _parse_utc(slot["start"])
        end = start + timedelta(
            minutes=poll.get("duration_minutes") or DEFAULT_DURATION_MINUTES
        )
        try:
            event = google_calendar.create_meet_event(
                event_id=calendar_event_id(poll["code"], slot["id"]),
                summary=poll.get("title") or "Opportunity Hack meeting",
                description=poll.get("description") or "",
                start_utc=slot["start"],
                end_utc=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                attendees=attendees,
            )
        except google_calendar.CalendarError as e:
            # Poll stays open: nothing was written, the admin can retry.
            return _err("calendar_failed", "Google Calendar: %s" % str(e), 502)
        updates.update({
            "calendar_event_id": event.get("event_id"),
            "meet_link": event.get("meet_link"),
            "calendar_html_link": event.get("html_link"),
            "invited_emails": [a["email"] for a in attendees],
        })
    updates["updated_at"] = updates["finalized_at"]
    db.collection(POLLS).document(poll["code"]).update(updates)
    poll.update(updates)
    _audit(
        "meet_poll_finalized",
        "Meeting '%s' (%s) set for %s UTC — %s"
        % (
            poll.get("title"), poll["code"], slot["start"],
            "%d invited via Google Calendar" % len(attendees)
            if send_invites else "no invites sent",
        ),
    )
    return {"success": True, "poll": _admin_poll(poll),
            "invited": len(attendees) if send_invites else 0}, 200
