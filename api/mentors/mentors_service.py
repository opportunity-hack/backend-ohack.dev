"""
Per-team mentor support panel — backend service.

Lets multiple approved mentors collaborate on a team during a hackathon:
mark coverage items done, leave public notes, raise/own/resolve flags,
rate judging-readiness on a 4-criterion rubric. All data is public-read
(reads come through the existing get_team / mentor-feed endpoint); writes
require a volunteer doc with volunteer_type='mentor', isSelected=True,
matched to the caller's PropelAuth identity for this event.

Slack notifications are deliberately quiet: only flag-raises, flag-resolutions,
and the team's first "all coverage covered" milestone broadcast to the team
channel; flag-raises also heartbeat the per-event mentor channel.
"""
import uuid
import logging
from datetime import datetime
from typing import Optional, Tuple

from firebase_admin import firestore

from db.db import get_db
from common.utils.firestore_helpers import clear_all_caches
from common.utils.slack import send_slack, send_slack_audit
from common.utils.firebase import get_hackathon_by_event_id
from services.users_service import get_propel_user_details_by_id
from services.volunteers_service import get_volunteer_by_email, get_volunteer_by_user_id
from services.teams_service import get_team

logger = logging.getLogger("myapp")


def clear_cache():
    """Bust the team/doc caches (so get_team — and this endpoint's own
    _build_response — is fresh) AND the hackathon event/list caches.

    Mentor writes change fields the CACHED event page (#teams) renders via
    get_single_hackathon_event: coverage X/6, mentor_open_flag_count, the judging
    consensus dots, last-touched. clear_all_caches() only clears the *registered*
    caches (e.g. teams' _GET_TEAM_CACHE), NOT get_single_hackathon_event's cache,
    so without this the event page lagged up to 10 min behind the always-fresh
    team page. Mirrors teams_service._clear_cache(). Lazy import avoids a circular
    import at module load."""
    clear_all_caches()
    try:
        from services.hackathons_service import clear_cache as clear_hackathon_caches
        clear_hackathon_caches()
    except Exception as e:  # pragma: no cover - best-effort cache bust
        logger.warning("mentors clear_cache: hackathon cache clear failed: %s", e)

# Canonical 6-item team-coverage list. Slugs MUST stay in lockstep with the
# frontend MENTOR_COVERAGE_ITEMS in src/components/Teams/mentorCoverage.js.
MENTOR_COVERAGE_ITEMS = [
    {"slug": "intro_made", "label": "Mentor introductions made",
     "blurb": "A mentor has connected with the team in their Slack channel."},
    {"slug": "scope_reviewed", "label": "Project scope reviewed",
     "blurb": "Scope is realistic for the hackathon window and ties to a real nonprofit need."},
    {"slug": "architecture_discussed", "label": "Architecture & tech stack discussed",
     "blurb": "Stack is sensible; nothing single-file or trivially shallow."},
    {"slug": "repo_health_checked", "label": "GitHub repo reviewed",
     "blurb": "Commits flowing, structure sensible, README starting to take shape."},
    {"slug": "criteria_walkthrough", "label": "Judging criteria walkthrough",
     "blurb": "Team has been shown the judging rubric (Scope, Documentation, Polish, Security, plus Accessibility) and knows what judges look for."},
    {"slug": "demo_devpost_reviewed", "label": "Demo video + DevPost reviewed",
     "blurb": "A mentor has reviewed at least a draft of the demo video and DevPost submission."},
]
MENTOR_COVERAGE_SLUGS = {item["slug"]: item for item in MENTOR_COVERAGE_ITEMS}

# Each coverage item wants independent sign-off from this many distinct mentors
# (capped here too — we don't want to pester a team with more than this). An item
# only counts toward the X/6 coverage total + the "fully covered" milestone once
# it reaches this many checks. Keep in lockstep with the frontend
# COVERAGE_TARGET_MENTORS in src/components/Teams/mentorCoverage.js.
COVERAGE_TARGET_MENTORS = 3

ALLOWED_FLAG_SEVERITIES = {"needs_attention", "blocked"}
# Five judging criteria. The first four are the main 10-pt rubric on
# /about/judges; "accessibility" is the special-category prize on the same
# page but mentors still coach for it, so it lives in the same set.
ALLOWED_CRITERIA = {"scope", "documentation", "polish", "security", "accessibility"}
ALLOWED_SCORES = {"green", "yellow", "red"}

MAX_NOTE_LEN = 1000
MAX_FLAG_BODY_LEN = 500
MAX_RATING_NOTE_LEN = 300


# -------- identity / auth --------------------------------------------------

def _resolve_caller(propel_user_id):
    """
    Return (email, oauth_user_id, name) for the calling PropelAuth user.
    Any of those three can be used to match against a volunteer doc.
    """
    try:
        details = get_propel_user_details_by_id(propel_user_id) or ()
        email = details[0] if len(details) > 0 else None
        oauth_user_id = details[1] if len(details) > 1 else None
        name = details[4] if len(details) > 4 else None
        return email, oauth_user_id, name
    except Exception as e:
        logger.warning("mentors._resolve_caller failed: %s", e)
        return None, None, None


def _find_mentor_volunteer(propel_user_id, event_id):
    """
    Resolve the caller's mentor volunteer doc for THIS event, trying every
    identity shape a doc may have been stored under. Mirrors handle_get
    (volunteers_views.py) so the panel gate matches the same docs the
    GET-application path already matches:

      1. raw PropelAuth UUID  — self-submitted apps store auth_user.user_id
         (the propel UUID) in the doc's `user_id` field. THIS is the common
         case and was the missing lookup that caused approved mentors with a
         form-email != login-email to 403.
      2. PropelAuth email     — form-entered email == login email.
      3. OAuth user_id        — legacy docs that stored oauth2|slack|... .

    Returns the first volunteer doc found (regardless of isSelected) or None.
    """
    if not propel_user_id or not event_id:
        return None

    # 1. Direct match on the raw propel UUID (how handle_submit stores user_id).
    try:
        v = get_volunteer_by_user_id(propel_user_id, event_id, "mentor")
        if v:
            return v
    except Exception as e:
        logger.warning("_find_mentor_volunteer: propel_id lookup failed: %s", e)

    email, oauth_user_id, _ = _resolve_caller(propel_user_id)

    # 2. Email match.
    if email:
        try:
            v = get_volunteer_by_email(email, event_id, "mentor")
            if v:
                return v
        except Exception as e:
            logger.warning("_find_mentor_volunteer: email lookup failed: %s", e)

    # 3. OAuth user_id match (legacy docs), only if it differs from the propel UUID.
    if oauth_user_id and oauth_user_id != propel_user_id:
        try:
            v = get_volunteer_by_user_id(oauth_user_id, event_id, "mentor")
            if v:
                return v
        except Exception as e:
            logger.warning("_find_mentor_volunteer: user_id lookup failed: %s", e)

    return None


def user_is_mentor_for_event(propel_user_id, event_id) -> bool:
    """
    True iff the caller has an approved (isSelected=True) mentor volunteer
    record for THIS event. Matches by raw propel UUID, then email, then OAuth
    user_id — any one is enough (see _find_mentor_volunteer).
    """
    v = _find_mentor_volunteer(propel_user_id, event_id)
    return bool(v and v.get("isSelected"))


def get_mentor_self_status(propel_user_id, event_id):
    """
    Returns { is_mentor, volunteer } for the GET /api/volunteer/<event_id>/me
    endpoint. The volunteer subset is intentionally lean (no PII beyond what
    the caller already knows about themselves).
    """
    chosen = _find_mentor_volunteer(propel_user_id, event_id)
    if chosen is None or not chosen.get("isSelected"):
        return {"is_mentor": False, "volunteer": None}
    return {
        "is_mentor": True,
        "volunteer": {
            "name": chosen.get("name"),
            "email": chosen.get("email"),
            "isSelected": True,
            "checkInTime": chosen.get("checkInTime"),
        },
    }


# -------- shared helpers ---------------------------------------------------

def _team_doc_or_404(team_id):
    db = get_db()
    ref = db.collection("teams").document(team_id)
    snap = ref.get()
    if not snap.exists:
        return None, None, None
    return ref, snap.to_dict() or {}, db


def _caller_attribution(propel_user_id):
    _, _, name = _resolve_caller(propel_user_id)
    return name or "A mentor"


def _touch(team_data, name, now_iso):
    """Mutate team_data in-place with denormalized last-touch info."""
    team_data["mentor_last_touched_at"] = now_iso
    team_data["mentor_last_touched_by_name"] = name


def _open_flag_count(flags):
    if not isinstance(flags, list):
        return 0
    return sum(1 for f in flags if not f.get("resolved_at"))


def _coverage_checks(entry):
    """Per-mentor checks map for one coverage item: {propel_id: {name, checked_at}}.

    Tolerates the legacy single-mentor shape ({done, checked_by_propel_id,
    checked_by_name, checked_at}) by surfacing it as one check, so existing data
    keeps counting until a mentor next touches the item (which migrates it)."""
    if not isinstance(entry, dict):
        return {}
    checks = entry.get("checks")
    if isinstance(checks, dict):
        return checks
    if entry.get("done"):
        pid = entry.get("checked_by_propel_id") or "_legacy"
        return {pid: {"name": entry.get("checked_by_name"),
                      "checked_at": entry.get("checked_at")}}
    return {}


def _coverage_done_count(checklist):
    """Count items that have reached COVERAGE_TARGET_MENTORS distinct checks."""
    if not isinstance(checklist, dict):
        return 0
    return sum(
        1 for slug in MENTOR_COVERAGE_SLUGS
        if len(_coverage_checks(checklist.get(slug))) >= COVERAGE_TARGET_MENTORS
    )


def _mentor_channel_for_event(event_id):
    """Resolve the per-event mentor Slack channel — explicit field, then
    fallback to a name-derived guess. Returns None if neither is available."""
    if not event_id:
        return None
    try:
        h = get_hackathon_by_event_id(event_id) or {}
        explicit = h.get("mentor_slack_channel")
        if explicit:
            return explicit
    except Exception as e:
        logger.warning("_mentor_channel_for_event: hackathon lookup failed: %s", e)
    # Fallback: <event_id>-mentors (deliberate convention; harmless if the
    # channel doesn't exist — send_slack will just log and move on).
    return f"{event_id}-mentors".replace("_", "-").lower()


def _project_link(team_data, team_id):
    event_id = team_data.get("hackathon_event_id") or ""
    if event_id:
        return f"https://ohack.dev/hack/{event_id}/team/{team_id}"
    return f"https://ohack.dev/hack/team/{team_id}"


def _build_response(team_id, **extra):
    """Round-trip the team through get_team so users[] DocumentReferences are
    flattened + enriched (Flask can't JSON-serialize raw refs)."""
    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh, **extra}, 200


# -------- coverage checklist ----------------------------------------------

def toggle_mentor_coverage(propel_user_id, team_id, item_slug, done, note=None):
    """
    Record (done=True) or clear (done=False) the CALLING mentor's own check on a
    coverage item. Each item collects independent sign-off from up to
    COVERAGE_TARGET_MENTORS distinct mentors — a mentor can only add or clear their
    own check, never another mentor's. An item counts toward the X/6 total (and the
    one-time "fully covered" Slack milestone) once it reaches the target. Quiet by
    default — only the first time every item is fully covered does Slack fire.
    """
    if item_slug not in MENTOR_COVERAGE_SLUGS:
        return {"error": f"Unknown coverage item: {item_slug}"}, 400
    if not isinstance(done, bool):
        return {"error": "Field 'done' must be a boolean"}, 400

    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id_guess = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id_guess):
        return {"error": "You must be an approved mentor for this event."}, 403

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    checklist = dict(team_data.get("mentor_checklist") or {})
    existing_entry = checklist.get(item_slug)
    checks = dict(_coverage_checks(existing_entry))  # propel_id -> {name, checked_at}
    is_legacy = isinstance(existing_entry, dict) and "checks" not in existing_entry

    # set(merge=True) deep-merges map fields, so we write ONLY the nested keys that
    # change. Removing my check means a DELETE_FIELD sentinel at my propel_id path
    # (popping the key wouldn't persist). Legacy single-mentor entries are migrated
    # into the `checks` map — and their top-level keys deleted — on first touch.
    entry_write = {"checks": {}}
    if is_legacy and isinstance(existing_entry, dict):
        for k in ("done", "checked_at", "checked_by_propel_id", "checked_by_name", "note"):
            if k in existing_entry:
                entry_write[k] = firestore.DELETE_FIELD
        for pid, val in checks.items():  # preserve any OTHER legacy checker
            if pid != propel_user_id:
                entry_write["checks"][pid] = val

    if done:
        if propel_user_id not in checks and len(checks) >= COVERAGE_TARGET_MENTORS:
            return ({"error": f"This item already has {COVERAGE_TARGET_MENTORS} mentor "
                              "checks — that's plenty, no need to add another."}, 409)
        my_check = {"name": name, "checked_at": now_iso}
        if note:
            my_check["note"] = str(note)[:MAX_RATING_NOTE_LEN]
        checks[propel_user_id] = my_check
        entry_write["checks"][propel_user_id] = my_check
    else:
        checks.pop(propel_user_id, None)
        entry_write["checks"][propel_user_id] = firestore.DELETE_FIELD

    # Reflect the change in our in-memory copy for counting + milestone.
    checklist[item_slug] = {"checks": checks}
    new_count = _coverage_done_count(checklist)
    total = len(MENTOR_COVERAGE_ITEMS)

    update = {"mentor_checklist": {item_slug: entry_write}}
    _touch(update, name, now_iso)

    # First-time-complete milestone: stamp it once so we never re-broadcast.
    fire_milestone = False
    if done and new_count == total and not team_data.get("mentor_coverage_completed_at"):
        update["mentor_coverage_completed_at"] = now_iso
        update["mentor_coverage_completed_by_name"] = name
        fire_milestone = True

    ref.set(update, merge=True)

    if fire_milestone:
        slack_channel = team_data.get("slack_channel")
        if slack_channel:
            try:
                send_slack(
                    message=(
                        f":sparkles: *{team_data.get('name', 'This team')}* has full mentor coverage "
                        f"— all {total} items signed off by {COVERAGE_TARGET_MENTORS} mentors each! "
                        f"Nice work team & mentors. Final coverage marker: *{name}*."
                    ),
                    channel=slack_channel,
                )
            except Exception as e:
                logger.error("toggle_mentor_coverage: milestone slack failed: %s", e)

    send_slack_audit(
        action="mentor_coverage_toggle",
        message=f"Team {team_id} coverage {item_slug}={done} by {name} ({new_count}/{total})",
        payload={"team_id": team_id, "item": item_slug, "done": done, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id, done=new_count, total=total)


# -------- notes feed ------------------------------------------------------

def add_mentor_note(propel_user_id, team_id, body):
    if not body or not isinstance(body, str):
        return {"error": "Note body is required"}, 400
    body = body.strip()
    if not body:
        return {"error": "Note body is required"}, 400
    if len(body) > MAX_NOTE_LEN:
        return {"error": f"Note body must be at most {MAX_NOTE_LEN} characters"}, 400

    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    notes = list(team_data.get("mentor_notes") or [])
    notes.append({
        "id": uuid.uuid4().hex,
        "created_at": now_iso,
        "author_propel_id": propel_user_id,
        "author_name": name,
        "body": body,
    })

    update = {"mentor_notes": notes}
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    send_slack_audit(
        action="mentor_note_add",
        message=f"Mentor note added on team {team_id} by {name}",
        payload={"team_id": team_id, "by": propel_user_id, "len": len(body)},
    )
    clear_cache()
    return _build_response(team_id)


def delete_mentor_note(propel_user_id, team_id, note_id):
    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    notes = list(team_data.get("mentor_notes") or [])
    target = next((n for n in notes if n.get("id") == note_id), None)
    if target is None:
        return {"error": "Note not found"}, 404
    if target.get("deleted_at"):
        return {"error": "Note already deleted"}, 409
    if target.get("author_propel_id") != propel_user_id:
        return {"error": "You can only delete your own notes."}, 403

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    target["deleted_at"] = now_iso
    target["deleted_by_propel_id"] = propel_user_id

    update = {"mentor_notes": notes}
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    send_slack_audit(
        action="mentor_note_delete",
        message=f"Mentor note {note_id} soft-deleted on team {team_id} by {name}",
        payload={"team_id": team_id, "note_id": note_id, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id)


# -------- flags -----------------------------------------------------------

def raise_mentor_flag(propel_user_id, team_id, severity, body):
    if severity not in ALLOWED_FLAG_SEVERITIES:
        return {"error": f"Severity must be one of: {sorted(ALLOWED_FLAG_SEVERITIES)}"}, 400
    if not body or not isinstance(body, str):
        return {"error": "Flag body is required"}, 400
    body = body.strip()
    if not body:
        return {"error": "Flag body is required"}, 400
    if len(body) > MAX_FLAG_BODY_LEN:
        return {"error": f"Flag body must be at most {MAX_FLAG_BODY_LEN} characters"}, 400

    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    flags = list(team_data.get("mentor_flags") or [])
    new_flag = {
        "id": uuid.uuid4().hex,
        "created_at": now_iso,
        "raised_by_propel_id": propel_user_id,
        "raised_by_name": name,
        "severity": severity,
        "body": body,
        "owner_propel_id": propel_user_id,
        "owner_name": name,
    }
    flags.append(new_flag)

    update = {
        "mentor_flags": flags,
        "mentor_open_flag_count": _open_flag_count(flags),
    }
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    # Loud: post into team channel + heartbeat the per-event mentor channel.
    team_name = team_data.get("name", "this team")
    project_link = _project_link(team_data, team_id)
    severity_emoji = ":warning:" if severity == "needs_attention" else ":rotating_light:"
    team_msg = (
        f"{severity_emoji} *Mentor flag raised on {team_name}* by *{name}*\n"
        f">{body}\n"
        f"Owned by *{name}*. Other mentors can take over from the team page.\n"
        f"{project_link}"
    )
    slack_channel = team_data.get("slack_channel")
    if slack_channel:
        try:
            send_slack(message=team_msg, channel=slack_channel)
        except Exception as e:
            logger.error("raise_mentor_flag: team-channel slack failed: %s", e)

    mentor_channel = _mentor_channel_for_event(event_id)
    if mentor_channel:
        try:
            send_slack(
                message=(
                    f"{severity_emoji} *{team_name}*: {body[:120]}"
                    f"{'...' if len(body) > 120 else ''}\n{project_link}"
                ),
                channel=mentor_channel,
            )
        except Exception as e:
            logger.warning("raise_mentor_flag: mentor-channel slack failed: %s", e)

    send_slack_audit(
        action="mentor_flag_raise",
        message=f"Flag raised on team {team_id} by {name} ({severity})",
        payload={"team_id": team_id, "severity": severity, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id, flag_id=new_flag["id"])


def take_over_mentor_flag(propel_user_id, team_id, flag_id):
    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    flags = list(team_data.get("mentor_flags") or [])
    target = next((f for f in flags if f.get("id") == flag_id), None)
    if target is None:
        return {"error": "Flag not found"}, 404
    if target.get("resolved_at"):
        return {"error": "Flag is already resolved"}, 409

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    target["owner_propel_id"] = propel_user_id
    target["owner_name"] = name

    update = {"mentor_flags": flags}
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    send_slack_audit(
        action="mentor_flag_takeover",
        message=f"Flag {flag_id} on team {team_id} taken over by {name}",
        payload={"team_id": team_id, "flag_id": flag_id, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id)


def resolve_mentor_flag(propel_user_id, team_id, flag_id, resolution_note):
    if not resolution_note or not str(resolution_note).strip():
        return {"error": "Resolution note is required"}, 400
    resolution_note = str(resolution_note).strip()[:MAX_FLAG_BODY_LEN]

    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    flags = list(team_data.get("mentor_flags") or [])
    target = next((f for f in flags if f.get("id") == flag_id), None)
    if target is None:
        return {"error": "Flag not found"}, 404
    if target.get("resolved_at"):
        return {"error": "Flag is already resolved"}, 409

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    target["resolved_at"] = now_iso
    target["resolved_by_propel_id"] = propel_user_id
    target["resolved_by_name"] = name
    target["resolution_note"] = resolution_note

    update = {
        "mentor_flags": flags,
        "mentor_open_flag_count": _open_flag_count(flags),
    }
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    team_name = team_data.get("name", "this team")
    slack_channel = team_data.get("slack_channel")
    if slack_channel:
        try:
            send_slack(
                message=(
                    f":white_check_mark: *Mentor flag resolved on {team_name}* by *{name}*\n"
                    f">{resolution_note}"
                ),
                channel=slack_channel,
            )
        except Exception as e:
            logger.error("resolve_mentor_flag: slack failed: %s", e)

    send_slack_audit(
        action="mentor_flag_resolve",
        message=f"Flag {flag_id} on team {team_id} resolved by {name}",
        payload={"team_id": team_id, "flag_id": flag_id, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id)


# -------- judging-readiness rating ----------------------------------------

def set_mentor_rating(propel_user_id, team_id, criterion, score, note=None):
    if criterion not in ALLOWED_CRITERIA:
        return {"error": f"Criterion must be one of: {sorted(ALLOWED_CRITERIA)}"}, 400
    if score not in ALLOWED_SCORES:
        return {"error": f"Score must be one of: {sorted(ALLOWED_SCORES)}"}, 400

    ref, team_data, _ = _team_doc_or_404(team_id)
    if ref is None:
        return {"error": "Team not found"}, 404
    event_id = team_data.get("hackathon_event_id")
    if not user_is_mentor_for_event(propel_user_id, event_id):
        return {"error": "You must be an approved mentor for this event."}, 403

    name = _caller_attribution(propel_user_id)
    now_iso = datetime.now().isoformat()
    ratings = list(team_data.get("mentor_ratings") or [])
    entry = {
        "rated_at": now_iso,
        "rated_by_propel_id": propel_user_id,
        "rated_by_name": name,
        "criterion": criterion,
        "score": score,
    }
    if note:
        entry["note"] = str(note).strip()[:MAX_RATING_NOTE_LEN]
    ratings.append(entry)

    update = {"mentor_ratings": ratings}
    _touch(update, name, now_iso)
    ref.set(update, merge=True)

    send_slack_audit(
        action="mentor_rating_set",
        message=f"Rating {criterion}={score} set on team {team_id} by {name}",
        payload={"team_id": team_id, "criterion": criterion, "score": score, "by": propel_user_id},
    )
    clear_cache()
    return _build_response(team_id)
