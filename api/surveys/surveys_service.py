"""Post-event / live-event feedback surveys.

Stores responses in the Firestore `surveys` collection (kept separate from the
existing peer-to-peer `feedback` collection). A response is keyed to an event
(`event_id`) and a mode:
  - `live`  — the event is currently happening (start <= now <= end)
  - `post`  — the event has ended (now > end)

Eligibility:
  - Logged-in volunteers who are `isSelected` for the event (mentor / judge /
    volunteer / sponsor) or have a hacker application — they're "trusted" and
    skip the CAPTCHA. Their role(s) are derived from the `volunteers` collection.
  - Everyone else (nonprofit partners, who have no flag yet, and any anonymous
    visitor) must pass the same Google reCAPTCHA v3 check the contact form uses.
"""
from typing import Dict, Any, List, Optional, Tuple
import uuid
import os
from datetime import datetime

import pytz

from db.db import get_db
from common.log import get_logger, warning

logger = get_logger(__name__)

SURVEY_COLLECTION = "surveys"
SURVEY_SLACK_CHANNEL = "feedback"
DEFAULT_TIMEZONE = "America/Phoenix"

# Roles given by the volunteers collection. A "hacker" record counts as soon as
# the application exists; the rest require isSelected=True to count.
_SELECTED_ONLY_ROLES = {"mentor", "judge", "volunteer", "sponsor"}

# Roles a submitted response may claim.
ALLOWED_ROLES = {"hacker", "mentor", "judge", "nonprofit", "volunteer", "organizer", "sponsor"}


def _now_iso() -> str:
    return datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).isoformat()


def _parse_event_date(date_str: Optional[str], tz, end_of_day: bool = False):
    """Parse a 'YYYY-MM-DD' string into a tz-aware datetime, or None."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        parts = date_str.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return None
    if end_of_day:
        return tz.localize(datetime(year, month, day, 23, 59, 59))
    return tz.localize(datetime(year, month, day, 0, 0, 0))


def compute_event_mode(event: Dict[str, Any]) -> str:
    """Return 'live', 'post', or 'upcoming' for the event's current window.

    Comparisons use the event's own timezone so a viewer elsewhere doesn't flip
    the mode early/late (mirrors the frontend `isHackathonExpired`).
    """
    tz_name = event.get("timezone") or DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone(DEFAULT_TIMEZONE)

    now = datetime.now(tz)
    start = _parse_event_date(event.get("start_date"), tz, end_of_day=False)
    end = _parse_event_date(event.get("end_date"), tz, end_of_day=True)

    if end and now > end:
        return "post"
    if start and now < start:
        return "upcoming"
    # Within the window, or dates missing — treat as live so feedback is reachable.
    return "live"


def _user_doc_id(event_id: str, mode: str, propel_user_id: str) -> str:
    """Deterministic doc id so a logged-in user's response upserts (no dupes)."""
    safe_uid = (propel_user_id or "anon").replace("/", "_")
    return f"{event_id}__{mode}__{safe_uid}"


def _resolve_user_identity(propel_user_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(email, oauth_user_id) for a PropelAuth user. Lazy import avoids a heavy
    module load at import time and keeps this module cheap to unit-test."""
    if not propel_user_id:
        return None, None
    try:
        from services.users_service import get_propel_user_details_by_id
        details = get_propel_user_details_by_id(propel_user_id) or ()
        email = details[0] if len(details) > 0 else None
        oauth_user_id = details[1] if len(details) > 1 else None
        return email, oauth_user_id
    except Exception as e:
        warning(logger, "survey: could not resolve user identity", exc_info=e)
        return None, None


def get_user_event_roles(propel_user_id: Optional[str], event_id: str) -> List[str]:
    """Roles the user is eligible to give feedback as for this event.

    Hacker counts on application; mentor/judge/volunteer/sponsor need
    isSelected=True. Matches the survey-eligibility rule. Uses equality-only
    queries (no composite index needed). Identity is matched the same three
    ways as `handle_get` in volunteers_views: propel UUID, email, OAuth user_id.
    """
    if not propel_user_id or not event_id:
        return []

    email, oauth_user_id = _resolve_user_identity(propel_user_id)
    db = get_db()
    roles = set()
    seen = set()

    def collect(field: str, value: Optional[str]):
        if not value:
            return
        try:
            query = (
                db.collection("volunteers")
                .where(field, "==", value)
                .where("event_id", "==", event_id)
            )
            for snap in query.stream():
                if snap.id in seen:
                    continue
                seen.add(snap.id)
                vol = snap.to_dict() or {}
                vtype = (vol.get("volunteer_type") or "").lower()
                if not vtype:
                    continue
                if vtype in _SELECTED_ONLY_ROLES and not vol.get("isSelected"):
                    continue
                roles.add(vtype)
        except Exception as e:
            warning(logger, "survey: volunteer role query failed", exc_info=e)

    for uid in {propel_user_id, oauth_user_id}:
        collect("user_id", uid)
    collect("email", email)
    return sorted(roles)


def allowed_roles_for(trusted: bool, eligible_roles: List[str]) -> List[str]:
    """Roles a submitter may claim.

    - A trusted (logged-in, isSelected) volunteer may only submit for the role(s)
      they were selected for — not every role.
    - Everyone else (nonprofit partners, who have no flag, and anonymous visitors)
      may only submit as a nonprofit, behind the CAPTCHA.
    """
    return list(eligible_roles) if trusted else ["nonprofit"]


def get_survey_context(event_id: str, propel_user_id: Optional[str]) -> Tuple[Dict[str, Any], int]:
    """What the frontend needs to render the right form: mode, the caller's
    eligible roles, whether a CAPTCHA is required, and a basic event summary."""
    from common.utils.firebase import get_hackathon_by_event_id

    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"success": False, "error": "Event not found"}, 404

    mode = compute_event_mode(event)
    logged_in = bool(propel_user_id)
    roles = get_user_event_roles(propel_user_id, event_id) if logged_in else []
    eligible = bool(roles)
    trusted = logged_in and eligible
    allowed = allowed_roles_for(trusted, roles)

    already_submitted = False
    if trusted and mode in ("live", "post"):
        try:
            doc_id = _user_doc_id(event_id, mode, propel_user_id)
            already_submitted = get_db().collection(SURVEY_COLLECTION).document(doc_id).get().exists
        except Exception as e:
            warning(logger, "survey: already-submitted check failed", exc_info=e)

    return {
        "success": True,
        "mode": mode,
        "logged_in": logged_in,
        "eligible": eligible if logged_in else None,
        "roles": roles,
        "allowed_roles": allowed,
        "primary_role": allowed[0] if allowed else None,
        "requires_captcha": not trusted,
        "already_submitted": already_submitted,
        "event": {
            "event_id": event.get("event_id") or event_id,
            "title": event.get("title"),
            "start_date": event.get("start_date"),
            "end_date": event.get("end_date"),
            "timezone": event.get("timezone") or DEFAULT_TIMEZONE,
        },
    }, 200


def submit_survey_response(
    event_id: str,
    propel_user_id: Optional[str],
    payload: Dict[str, Any],
    ip_address: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    """Validate + persist one feedback response. Returns (body, status_code)."""
    from common.utils.firebase import get_hackathon_by_event_id

    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"success": False, "error": "Event not found"}, 404

    mode = compute_event_mode(event)
    if mode not in ("live", "post"):
        return {"success": False, "error": "This event's feedback form is not open yet."}, 403

    role = (payload.get("role") or "").strip().lower()
    if role not in ALLOWED_ROLES:
        return {"success": False, "error": "A valid role is required."}, 400

    answers = payload.get("answers")
    if not isinstance(answers, dict) or not answers:
        return {"success": False, "error": "No answers were provided."}, 400

    logged_in = bool(propel_user_id)
    roles = get_user_event_roles(propel_user_id, event_id) if logged_in else []
    trusted = logged_in and bool(roles)

    # Enforce role scope: selected volunteers may only submit for a role they
    # were selected for; everyone else may only submit as a nonprofit.
    if role not in set(allowed_roles_for(trusted, roles)):
        return {
            "success": False,
            "error": "You can only submit feedback for the role you're selected for.",
        }, 403

    # CAPTCHA gate for everyone who isn't a known, selected volunteer.
    if not trusted:
        from api.contact.contact_service import verify_recaptcha
        token = payload.get("recaptchaToken")
        if not verify_recaptcha(token) and os.environ.get("FLASK_ENV") != "development":
            warning(logger, "survey: reCAPTCHA verification failed", event_id=event_id)
            return {"success": False, "error": "reCAPTCHA verification failed"}, 400

    email = None
    if logged_in:
        email, _ = _resolve_user_identity(propel_user_id)
    if not email:
        provided = payload.get("email")
        if isinstance(provided, str) and "@" in provided:
            email = provided.strip()

    now = _now_iso()
    db = get_db()
    record = {
        "event_id": event_id,
        "mode": mode,
        "role": role,
        "answers": answers,
        "user_id": propel_user_id,
        "email": email,
        "is_anonymous": not logged_in,
        "eligible_roles": roles,
        "source": payload.get("source") or "survey",
        "ip_address": ip_address,
        "updated_at": now,
    }

    is_update = False
    if logged_in:
        # Deterministic id → a user's response upserts. Write the whole doc
        # (NOT merge=True — that deep-merges the answers map and would leave
        # behind keys the user cleared) while carrying created_at forward.
        doc_id = _user_doc_id(event_id, mode, propel_user_id)
        ref = db.collection(SURVEY_COLLECTION).document(doc_id)
        snap = ref.get()
        if snap.exists:
            is_update = True
            record["created_at"] = (snap.to_dict() or {}).get("created_at") or now
        else:
            record["created_at"] = now
        ref.set(record)
    else:
        doc_id = str(uuid.uuid4())
        record["created_at"] = now
        db.collection(SURVEY_COLLECTION).document(doc_id).set(record)

    _notify_submission(event, record, doc_id)
    return (
        {"success": True, "id": doc_id, "mode": mode, "updated": is_update},
        200 if is_update else 201,
    )


def _notify_submission(event: Dict[str, Any], record: Dict[str, Any], doc_id: str) -> None:
    """Audit every response; ping the #feedback Slack channel for *live*
    feedback only, so organizers can react in real time without post-event spam."""
    try:
        from common.utils.slack import send_slack_audit
        send_slack_audit(
            action="survey_response",
            message=f"Survey response for {record.get('event_id')} ({record.get('mode')}, {record.get('role')})",
            payload={"id": doc_id},
        )
    except Exception:
        pass

    if record.get("mode") != "live":
        return

    try:
        from common.utils.slack import send_slack
        answers = record.get("answers") or {}
        lines = [
            f"*New live feedback* — {event.get('title') or record.get('event_id')}",
            f"*Role:* {record.get('role')}",
        ]
        rating = answers.get("overall_rating")
        if rating is not None:
            lines.append(f"*How's it going:* {rating}/5")
        for key, label in (
            ("hacker_blocked", "Blocked"),
            ("mentor_team_concern", "Team concern"),
            ("vol_live_issue", "Issue"),
            ("npo_team_waiting", "NPO blocking a team"),
            ("to_improve", "Should fix"),
        ):
            val = answers.get(key)
            if isinstance(val, dict):
                val = val.get("value") or val.get("note")
            if val:
                lines.append(f"*{label}:* {val}")
        send_slack(
            message="\n".join(lines),
            channel=SURVEY_SLACK_CHANNEL,
            username="Feedback Bot",
            icon_emoji=":memo:",
        )
    except Exception as e:
        warning(logger, "survey: live Slack notify failed", exc_info=e)


def get_event_survey_responses(event_id: str, mode: Optional[str] = None) -> Dict[str, Any]:
    """Admin: all responses for an event (PII-light — drops ip_address)."""
    db = get_db()
    query = db.collection(SURVEY_COLLECTION).where("event_id", "==", event_id)
    if mode:
        query = query.where("mode", "==", mode)

    responses = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        data.pop("ip_address", None)
        responses.append(data)

    responses.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"success": True, "responses": responses, "count": len(responses)}


def get_event_survey_summary(event_id: str) -> Dict[str, Any]:
    """Admin: light aggregate — counts by mode/role + averages of the two
    cross-event segmenting scales (overall_rating, would_return)."""
    responses = get_event_survey_responses(event_id)["responses"]
    summary = {
        "count": len(responses),
        "by_mode": {},
        "by_role": {},
        "overall_rating": {"count": 0, "average": None},
        "would_return": {"count": 0, "average": None},
    }
    rating_sum = 0.0
    return_sum = 0.0
    for resp in responses:
        summary["by_mode"][resp.get("mode")] = summary["by_mode"].get(resp.get("mode"), 0) + 1
        summary["by_role"][resp.get("role")] = summary["by_role"].get(resp.get("role"), 0) + 1
        answers = resp.get("answers") or {}
        overall = answers.get("overall_rating")
        if isinstance(overall, (int, float)):
            summary["overall_rating"]["count"] += 1
            rating_sum += overall
        would_return = answers.get("would_return")
        if isinstance(would_return, (int, float)):
            summary["would_return"]["count"] += 1
            return_sum += would_return
    if summary["overall_rating"]["count"]:
        summary["overall_rating"]["average"] = round(rating_sum / summary["overall_rating"]["count"], 2)
    if summary["would_return"]["count"]:
        summary["would_return"]["average"] = round(return_sum / summary["would_return"]["count"], 2)
    return {"success": True, "summary": summary}


def get_cross_event_survey_overview() -> Dict[str, Any]:
    """Admin: cross-event aggregate for the 'Compare events' view.

    One scan of the surveys collection grouped by `event_id`, with per-event
    averages of the two universal scales (overall_rating, would_return) plus
    mode/role counts and the first/last response timestamps. Joined with
    hackathon metadata (title / dates / timezone). Aggregates only — no
    per-response data and no PII.
    """
    db = get_db()
    by_event: Dict[str, Dict[str, Any]] = {}
    totals: Dict[str, Any] = {"responses": 0, "by_mode": {}, "by_role": {}}

    for doc in db.collection(SURVEY_COLLECTION).stream():
        data = doc.to_dict() or {}
        event_id = data.get("event_id")
        if not event_id:
            continue
        ev = by_event.get(event_id)
        if ev is None:
            ev = by_event[event_id] = {
                "count": 0,
                "by_mode": {},
                "by_role": {},
                "_rating_sum": 0.0, "_rating_n": 0,
                "_return_sum": 0.0, "_return_n": 0,
                "first_response": None,
                "last_response": None,
            }
        ev["count"] += 1
        totals["responses"] += 1

        mode = data.get("mode")
        if mode:
            ev["by_mode"][mode] = ev["by_mode"].get(mode, 0) + 1
            totals["by_mode"][mode] = totals["by_mode"].get(mode, 0) + 1
        role = data.get("role")
        if role:
            ev["by_role"][role] = ev["by_role"].get(role, 0) + 1
            totals["by_role"][role] = totals["by_role"].get(role, 0) + 1

        answers = data.get("answers") or {}
        overall = answers.get("overall_rating")
        if isinstance(overall, (int, float)):
            ev["_rating_sum"] += overall
            ev["_rating_n"] += 1
        would_return = answers.get("would_return")
        if isinstance(would_return, (int, float)):
            ev["_return_sum"] += would_return
            ev["_return_n"] += 1

        created = data.get("created_at")
        if isinstance(created, str) and created:
            # Surveys are born-digital ISO; strip the CSV-export sentinel defensively.
            if created.startswith("__Timestamp__"):
                created = created[len("__Timestamp__"):]
            if ev["first_response"] is None or created < ev["first_response"]:
                ev["first_response"] = created
            if ev["last_response"] is None or created > ev["last_response"]:
                ev["last_response"] = created

    # Join event metadata (title / dates / tz). Lazy import avoids a heavy module
    # load at import time and any circular import (mirrors _resolve_user_identity).
    event_meta: Dict[str, Dict[str, Any]] = {}
    try:
        from services.hackathons_service import get_hackathon_list
        for h in (get_hackathon_list().get("hackathons") or []):
            hid = h.get("event_id") or h.get("id")
            if hid:
                event_meta[hid] = h
    except Exception as e:  # pragma: no cover - defensive
        warning(logger, "survey-overview: hackathon metadata join failed", exc_info=e)

    events: List[Dict[str, Any]] = []
    for event_id, ev in by_event.items():
        meta = event_meta.get(event_id) or {}
        rating_avg = round(ev["_rating_sum"] / ev["_rating_n"], 2) if ev["_rating_n"] else None
        return_avg = round(ev["_return_sum"] / ev["_return_n"], 2) if ev["_return_n"] else None
        events.append({
            "event_id": event_id,
            "title": meta.get("title") or event_id,
            "start_date": meta.get("start_date"),
            "end_date": meta.get("end_date"),
            "timezone": meta.get("timezone") or DEFAULT_TIMEZONE,
            "count": ev["count"],
            "by_mode": ev["by_mode"],
            "by_role": ev["by_role"],
            "overall_rating": {"count": ev["_rating_n"], "average": rating_avg},
            "would_return": {"count": ev["_return_n"], "average": return_avg},
            "first_response": ev["first_response"],
            "last_response": ev["last_response"],
        })

    # Chronological; events with no known start_date fall to the end.
    events.sort(key=lambda e: (e.get("start_date") or "9999", e.get("first_response") or ""))

    return {
        "success": True,
        "totals": {
            "responses": totals["responses"],
            "events": len(events),
            "by_mode": totals["by_mode"],
            "by_role": totals["by_role"],
        },
        "events": events,
    }
