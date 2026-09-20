"""
Team project write-ups + submission deadlines (Sep 2026 — the team dashboard
that replaces DevPost as the team's single home). See docs/plans/
team-dashboard-devpost-replacement.md (frontend repo) Part 3 for the full
contract; this module is the backend half.

Ownership split (deliberately kept separate from api.teams.teams_service):
  - Everything here is SELF-SERVE and deadline-aware: a team member writes
    their own project_* fields, subject to the event's submission window.
  - api.teams.teams_service.edit_team stays the ADMIN write path (no deadline
    gate, no membership check — org-permission gated at the route).
  - self_serve_team_edit (below) is the bridge used by the existing
    /devpost and /demo-video routes, which used to call edit_team directly
    with NO membership check at all (Part 9 bug #1 — any logged-in user could
    overwrite any team's DevPost link or demo video).

Sanitization decision: project_tagline/project_story are stored as raw
markdown. The frontend renders them with react-markdown WITHOUT rehype-raw,
so any HTML tag in the stored text is inert on read. sanitize_markdown()
(common.utils.validators) is defence-in-depth only, for the case this content
is ever rendered somewhere less careful: it strips a small denylist of
dangerous tags/attributes and neutralizes javascript:/vbscript:/data: link
targets. It deliberately does NOT strip generic "<" — code like
"List<String>" in a project story must survive untouched.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from db.db import get_db
from common.utils.firestore_helpers import clear_all_caches
from common.utils.slack import send_slack, send_slack_audit
from common.utils.firebase import get_hackathon_by_event_id
from common.utils.validators import (
    normalize_deadline_iso,
    sanitize_markdown,
    sanitize_string,
    validate_https_url,
)
from services.teams_service import get_team

logger = logging.getLogger("myapp")

# Sub-fields of the team doc this module owns (partial-update semantics: a
# save only ever touches the keys present in the request payload).
PROJECT_FIELDS = (
    "project_tagline",
    "project_story",
    "project_built_with",
    "project_links",
    "project_thumbnail_url",
    "project_images",
)

PROJECT_LIMITS = {
    "tagline": 140,
    "story": 20000,
    "built_with_n": 25,
    "built_with_len": 30,
    "links_n": 10,
    "label": 40,
    "url": 2048,
    "images_n": 8,
}

# A submitted project's status never regresses to "draft" by a hacker's own
# action; only submit_project (draft -> submitted|late) and an admin override
# via PATCH /api/team/edit change it after that.
SUBMITTED_STATUSES = {"submitted", "late"}

# submit_project requires at least a tagline and a story — an empty write-up
# with just a demo video isn't a "project" yet.
REQUIRED_SUBMIT_FIELDS = ("project_tagline", "project_story")

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def clear_cache() -> None:
    """Bust the team/doc caches AND the hackathon event caches.

    Mirrors api/mentors/mentors_service.py's clear_cache(): a project save
    changes fields the CACHED event page (get_single_hackathon_event) also
    renders (submission tag, tagline), so both the registered per-function
    caches AND the hackathon service's own cache need clearing. Lazy import
    avoids a circular import at module load.
    """
    clear_all_caches()
    try:
        from services.hackathons_service import clear_cache as clear_hackathon_caches
        clear_hackathon_caches()
    except Exception as e:  # pragma: no cover - best-effort cache bust
        logger.warning("submissions clear_cache: hackathon cache clear failed: %s", e)


def _notifications_disabled() -> bool:
    """Mirror of the ENVIRONMENT=test gate used across services (see
    services/volunteers_service.py) so unit tests never hit the real Slack API."""
    return os.environ.get("ENVIRONMENT") == "test"


def _cdn_server() -> str:
    return os.getenv("CDN_SERVER", "https://cdn.ohack.dev").rstrip("/")


def _safe_normalize_deadline(value, tz_name, label):
    """None/"" -> None; a naive or "Z"-suffixed value is normalized to an
    aware ISO string; an unparseable value is logged and treated as absent
    rather than raising (HIGH finding #5 — datetime.fromisoformat on a naive
    stored string, compared against an aware `now`, raises TypeError; a
    "Z"-suffixed string raises ValueError on Python 3.9/3.10)."""
    if not value:
        return None
    try:
        return normalize_deadline_iso(value, tz_name)
    except ValueError as e:
        logger.warning("compute_submission_window: unparseable %s %r: %s", label, value, e)
        return None


def compute_submission_window(event, now=None):
    """{"state": open|late|closed|no_deadline, "submission", "late_until", "now", "timezone"}.

    `event` is a hackathon dict (as returned by get_hackathon_by_event_id) —
    may be {} if the event couldn't be resolved, in which case this degrades
    to no_deadline rather than raising. `now` is an injectable
    timezone-aware datetime for tests; defaults to real UTC now.
    """
    event = event or {}
    tz_name = event.get("timezone") or "America/Phoenix"
    now_dt = now or datetime.now(timezone.utc)
    deadlines = event.get("deadlines") or {}
    submission = _safe_normalize_deadline(deadlines.get("submission"), tz_name, "submission")
    late_until = _safe_normalize_deadline(deadlines.get("late_submission_until"), tz_name, "late_submission_until")

    if not submission:
        return {
            "state": "no_deadline",
            "submission": None,
            "late_until": late_until,
            "now": now_dt.isoformat(),
            "timezone": tz_name,
        }

    submission_dt = datetime.fromisoformat(submission)
    if now_dt <= submission_dt:
        state = "open"
    elif late_until and now_dt <= datetime.fromisoformat(late_until):
        state = "late"
    else:
        state = "closed"

    return {
        "state": state,
        "submission": submission,
        "late_until": late_until,
        "now": now_dt.isoformat(),
        "timezone": tz_name,
    }


def submissions_closed(window) -> bool:
    return (window or {}).get("state") == "closed"


def _team_or_404(team_id):
    """(ref, team_dict|None). Mirrors api/mentors/mentors_service.py's
    _team_doc_or_404 but returns the id-stamped dict instead of a 3-tuple with
    the db handle (callers here don't need it)."""
    db = get_db()
    ref = db.collection("teams").document(team_id)
    snap = ref.get()
    if not snap.exists:
        return ref, None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return ref, data


def _authorize_team_write(propel_user_id, team_id, admin=False, enforce_deadline=True):
    """Shared gate for every self-serve team write in this module.

    Returns (error, team, event, window) where `error` is either None (proceed)
    or a ready-to-return (payload, status) tuple:
      - (.., 404) team not found
      - (.., 403) {"error": "not_team_member"} — caller isn't on the team and
        isn't an admin
      - (.., 409) {"error": "submissions_closed", "deadline", "late_until",
        "now"} — enforce_deadline=True, caller isn't an admin, and the
        window has closed

    Admins bypass both the membership check and the deadline gate.
    """
    from api.teams.teams_service import user_is_on_team

    _ref, team = _team_or_404(team_id)
    if team is None:
        return ({"error": "Team not found"}, 404), None, None, None

    if not admin and not user_is_on_team(propel_user_id, team_id):
        return ({"error": "not_team_member"}, 403), team, None, None

    event = get_hackathon_by_event_id(team.get("hackathon_event_id")) or {}
    window = compute_submission_window(event)

    if enforce_deadline and not admin and submissions_closed(window):
        return (
            {
                "error": "submissions_closed",
                "deadline": window.get("submission"),
                "late_until": window.get("late_until"),
                "now": window.get("now"),
            },
            409,
        ), team, event, window

    return None, team, event, window


def _validate_own_cdn_image(url, team_id, existing_urls):
    """True/(False, reason) for a project_thumbnail_url / project_images[]
    entry. Must be an own-CDN URL under teams/<team_id>/ (the existing
    POST /api/messages/upload-image route, directory=teams/<team_id>/project).
    A URL already on the team's doc is trusted without re-verifying against
    GCS (it was verified when first saved); a NEW url must exist, be an
    image, and be under the size cap."""
    prefix = f"{_cdn_server()}/teams/{team_id}/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return False, f"must be an ohack CDN URL under teams/{team_id}/"
    if url in existing_urls:
        return True, None
    try:
        from common.utils.cdn import get_blob_metadata
        blob_path = url[len(_cdn_server()) + 1:]
        meta = get_blob_metadata(blob_path)
    except Exception as e:
        logger.warning("_validate_own_cdn_image: failed to verify %s: %s", url, e)
        return False, "upload_not_found"
    if not meta.get("exists"):
        return False, "upload_not_found"
    if not (meta.get("content_type") or "").startswith("image/"):
        return False, "must be an image"
    if (meta.get("size") or 0) > MAX_IMAGE_BYTES:
        return False, "must be 5MB or smaller"
    return True, None


def validate_project_payload(payload, team_id, existing=None):
    """(clean, errors[{field, reason}]) — a partial update: only keys present
    in `payload` are validated/returned. `existing` is the team's current doc
    (so an already-saved thumbnail/image URL isn't re-verified against GCS)."""
    if not isinstance(payload, dict):
        return {}, [{"field": "payload", "reason": "must be an object"}]

    existing = existing or {}
    errors = []
    clean = {}

    if "project_tagline" in payload:
        val = payload.get("project_tagline")
        if val in (None, ""):
            clean["project_tagline"] = None
        elif not isinstance(val, str) or len(val) > PROJECT_LIMITS["tagline"]:
            errors.append({"field": "project_tagline", "reason": f"must be a string <= {PROJECT_LIMITS['tagline']} chars"})
        else:
            clean["project_tagline"] = sanitize_markdown(val, PROJECT_LIMITS["tagline"])

    if "project_story" in payload:
        val = payload.get("project_story")
        if val in (None, ""):
            clean["project_story"] = None
        elif not isinstance(val, str) or len(val) > PROJECT_LIMITS["story"]:
            errors.append({"field": "project_story", "reason": f"must be a string <= {PROJECT_LIMITS['story']} chars"})
        else:
            clean["project_story"] = sanitize_markdown(val, PROJECT_LIMITS["story"])

    if "project_built_with" in payload:
        val = payload.get("project_built_with")
        if val is None:
            clean["project_built_with"] = []
        elif (
            not isinstance(val, list)
            or len(val) > PROJECT_LIMITS["built_with_n"]
            or not all(isinstance(x, str) and len(x) <= PROJECT_LIMITS["built_with_len"] for x in val)
        ):
            errors.append({
                "field": "project_built_with",
                "reason": f"must be a list of at most {PROJECT_LIMITS['built_with_n']} strings, each <= {PROJECT_LIMITS['built_with_len']} chars",
            })
        else:
            clean["project_built_with"] = [sanitize_string(x, PROJECT_LIMITS["built_with_len"]) for x in val]

    if "project_links" in payload:
        val = payload.get("project_links")
        if val is None:
            clean["project_links"] = []
        elif not isinstance(val, list) or len(val) > PROJECT_LIMITS["links_n"]:
            errors.append({"field": "project_links", "reason": f"must be a list of at most {PROJECT_LIMITS['links_n']} items"})
        else:
            cleaned_links = []
            bad = False
            for link in val:
                if not isinstance(link, dict):
                    bad = True
                    break
                label = link.get("label", "")
                url = link.get("url", "")
                if not isinstance(label, str) or len(label) > PROJECT_LIMITS["label"]:
                    bad = True
                    break
                if not validate_https_url(url, PROJECT_LIMITS["url"]):
                    bad = True
                    break
                cleaned_links.append({"label": sanitize_string(label, PROJECT_LIMITS["label"]), "url": url})
            if bad:
                errors.append({
                    "field": "project_links",
                    "reason": f"each link needs a label <= {PROJECT_LIMITS['label']} chars and an https url <= {PROJECT_LIMITS['url']} chars",
                })
            else:
                clean["project_links"] = cleaned_links

    existing_image_urls = {existing.get("project_thumbnail_url")} | set(existing.get("project_images") or [])
    existing_image_urls.discard(None)

    if "project_thumbnail_url" in payload:
        val = payload.get("project_thumbnail_url")
        if val in (None, ""):
            clean["project_thumbnail_url"] = None
        else:
            ok, reason = _validate_own_cdn_image(val, team_id, existing_image_urls)
            if not ok:
                errors.append({"field": "project_thumbnail_url", "reason": reason})
            else:
                clean["project_thumbnail_url"] = val

    if "project_images" in payload:
        val = payload.get("project_images")
        if val is None:
            clean["project_images"] = []
        elif not isinstance(val, list) or len(val) > PROJECT_LIMITS["images_n"]:
            errors.append({"field": "project_images", "reason": f"must be a list of at most {PROJECT_LIMITS['images_n']} images"})
        else:
            cleaned_images = []
            bad_reason = None
            for url in val:
                ok, reason = _validate_own_cdn_image(url, team_id, existing_image_urls)
                if not ok:
                    bad_reason = reason
                    break
                cleaned_images.append(url)
            if bad_reason:
                errors.append({"field": "project_images", "reason": bad_reason})
            else:
                clean["project_images"] = cleaned_images

    return clean, errors


def save_project(propel_user_id, team_id, payload, admin=False):
    """Partial update of project_* fields. Sets project_submission_status to
    'draft' on the very first save (never regressed to draft afterward).
    409s (via _authorize_team_write) once the submission window has closed,
    unless the caller is an admin."""
    err, team, _event, window = _authorize_team_write(propel_user_id, team_id, admin=admin, enforce_deadline=True)
    if err:
        return err

    clean, errors = validate_project_payload(payload, team_id, existing=team)
    if errors:
        return {"error": "invalid_project", "errors": errors}, 400

    update = dict(clean)
    update["project_updated_at"] = datetime.now(timezone.utc).isoformat()
    if not team.get("project_submission_status"):
        update["project_submission_status"] = "draft"

    db = get_db()
    db.collection("teams").document(team_id).set(update, merge=True)
    clear_cache()
    send_slack_audit(
        action="project_save",
        message=f"Team {team_id} saved project fields: {sorted(clean.keys())}",
        payload={"team_id": team_id},
    )

    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh, "window": window}, 200


def submit_project(propel_user_id, team_id, admin=False):
    """Marks the project submitted|late. Idempotent — resubmitting an already
    submitted/late project is a no-op 200 with already_submitted=True, EVEN
    once the submission window has fully closed (LOW finding #10 — the
    already-submitted check must run before the deadline gate, not after, or
    a team that submitted on time gets a spurious 409 just by revisiting the
    dashboard after close). A fresh (not-yet-submitted) team is still blocked
    with 409 once the window is fully closed, unless the caller is an admin,
    in which case the forced submission is recorded as 'late' regardless of
    how long past close it is."""
    err, team, _event, window = _authorize_team_write(propel_user_id, team_id, admin=admin, enforce_deadline=False)
    if err:
        return err

    if team.get("project_submission_status") in SUBMITTED_STATUSES:
        fresh = (get_team(team_id) or {}).get("team") or {}
        return {"success": True, "already_submitted": True, "team": fresh}, 200

    if not admin and submissions_closed(window):
        return (
            {
                "error": "submissions_closed",
                "deadline": window.get("submission"),
                "late_until": window.get("late_until"),
                "now": window.get("now"),
            },
            409,
        )

    missing = [f for f in REQUIRED_SUBMIT_FIELDS if not (team.get(f) or "").strip()]
    if missing:
        return {"error": "incomplete", "missing": missing}, 400

    status = "late" if window.get("state") in ("late", "closed") else "submitted"
    now_iso = datetime.now(timezone.utc).isoformat()

    db = get_db()
    db.collection("teams").document(team_id).set(
        {"project_submission_status": status, "project_submitted_at": now_iso},
        merge=True,
    )
    clear_cache()

    slack_channel = team.get("slack_channel")
    if slack_channel and not _notifications_disabled():
        label = "a little late, but it's in" if status == "late" else "on time"
        try:
            send_slack(
                message=f":rocket: Project submitted — {label}! You can keep editing the write-up until submissions fully close.",
                channel=slack_channel,
            )
        except Exception as e:
            logger.warning("submit_project: send_slack failed for team %s: %s", team_id, e)
    send_slack_audit(
        action="project_submit",
        message=f"Team {team_id} submitted project (status={status})",
        payload={"team_id": team_id, "status": status},
    )

    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh, "status": status}, 200


def self_serve_team_edit(propel_user_id, team_id, fields, admin=False):
    """Bridge for the /devpost and /demo-video routes (Part 9 bug #1 fix):
    gate on team membership + the submission deadline exactly like
    save_project, then delegate the actual write to the existing admin
    edit_team (which already knows how to stamp *_submitted timestamps for
    devpost_link/demo_video_url). Lazy import: api.teams.teams_service must
    never import this module, so importing it here (not at module top) keeps
    the dependency one-directional.

    MEDIUM finding #4: edit_team only busts the generic per-function caches
    (common.utils.firestore_helpers.clear_all_caches) — it has no reason to
    know about the separately-cached get_single_hackathon_event (10-min TTL),
    so a self-serve DevPost/demo-video save left the event page showing stale
    data for up to 10 minutes. Call this module's own clear_cache() (which
    busts both) after edit_team returns.
    """
    err, _team, _event, _window = _authorize_team_write(propel_user_id, team_id, admin=admin, enforce_deadline=True)
    if err:
        return err

    from api.teams.teams_service import edit_team
    edit_result = edit_team({"id": team_id, **fields})
    clear_cache()
    fresh = (get_team(team_id) or {}).get("team") or {}
    return {**edit_result, "team": fresh}


def set_mentor_help_wanted(propel_user_id, team_id, open_flag, admin=False):
    """Team-facing 'open to mentors / heads-down' signal. No deadline gate —
    a team can flip this any time, including after submitting. Signal only:
    it changes no other behavior (see mentor surfaces in the frontend for the
    quiet UI treatment)."""
    if not isinstance(open_flag, bool):
        return {"error": "'open' must be a boolean"}, 400

    err, _team, _event, _window = _authorize_team_write(propel_user_id, team_id, admin=admin, enforce_deadline=False)
    if err:
        return err

    name = None
    try:
        from services.users_service import get_propel_user_details_by_id
        details = get_propel_user_details_by_id(propel_user_id) or ()
        name = details[4] if len(details) > 4 else None
    except Exception as e:
        logger.warning("set_mentor_help_wanted: could not resolve caller name: %s", e)

    now_iso = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.collection("teams").document(team_id).set(
        {
            "mentor_help_wanted": open_flag,
            "mentor_help_wanted_updated_at": now_iso,
            "mentor_help_wanted_updated_by_name": name,
        },
        merge=True,
    )
    clear_cache()
    send_slack_audit(
        action="mentor_help_wanted",
        message=f"Team {team_id} set mentor_help_wanted={open_flag}",
        payload={"team_id": team_id, "open": open_flag, "by": propel_user_id},
    )

    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh}, 200


def get_submission_window_for_event(event_id):
    """Public GET /api/hackathons/<event_id>/submissions/window."""
    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"error": "Event not found"}, 404
    return compute_submission_window(event), 200


# ---------------------------------------------------------------------------
# Deadline reminders (T-24h/6h/1h Slack nudges) — admin button + hourly cron.
# ---------------------------------------------------------------------------

REMINDER_KINDS = {"submission"}
REMINDER_HOURS = {24, 6, 1}


def build_reminder_message(team, event, deadline_iso, hours_before):
    """A tailored nudge naming only what THIS team still owes, or None when
    the team has nothing left to do (already submitted — never nag a done team)."""
    if team.get("project_submission_status") in SUBMITTED_STATUSES:
        return None

    missing = []
    if not (team.get("project_tagline") or "").strip():
        missing.append("write a tagline for your project")
    if not (team.get("project_story") or "").strip():
        missing.append("write your project story")
    if not team.get("demo_video_url"):
        missing.append("add a demo video")
    missing.append("submit your project")

    bullets = "\n".join(f"• {item}" for item in missing)
    event_id = event.get("event_id") or team.get("hackathon_event_id") or ""
    link = f"https://www.ohack.dev/hack/{event_id}/manageteam"
    hours_label = f"{hours_before} hour" + ("s" if hours_before != 1 else "")

    return (
        f":alarm_clock: *{hours_label} left to submit your project!*\n"
        f"Still to do:\n{bullets}\n"
        f"{link}"
    )


def send_deadline_reminders(event_id, kind, hours_before, *, only_if_due=False, force=False, actor="cron"):
    """Sends a Slack reminder to every active team's channel for one
    (kind, hours_before) pair. Idempotent per event+kind+hours_before unless
    `force` — `reminders_sent[f"{kind}_{hours_before}h"]` on the hackathon doc
    is the idempotency key. `only_if_due` (used by the hourly cron) skips
    silently ({"success": true, "skipped": "not_due"}) outside the
    one-hour-wide [deadline - hours_before, deadline - hours_before + 1h)
    window rather than erroring, so the cron can call this for every
    (event, hours) pair every hour without spamming teams the other 23 hours
    of the day. The window is deliberately only one hour wide (it used to be
    [deadline - hours_before, deadline), i.e. open all the way up to the
    deadline itself) — with the old window, a deadline set with only a few
    hours' notice would have its 24h AND 6h tiers both fall "due" on the very
    first cron tick and fire together (LOW finding #9)."""
    if kind not in REMINDER_KINDS:
        return {"error": f"kind must be one of {sorted(REMINDER_KINDS)}"}, 400
    try:
        hours_before = int(hours_before)
    except (TypeError, ValueError):
        return {"error": f"hours_before must be one of {sorted(REMINDER_HOURS)}"}, 400
    if hours_before not in REMINDER_HOURS:
        return {"error": f"hours_before must be one of {sorted(REMINDER_HOURS)}"}, 400

    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"error": "Event not found"}, 404

    deadline_iso = (event.get("deadlines") or {}).get("submission")
    if not deadline_iso:
        return {"error": "no_deadline"}, 409

    reminder_key = f"{kind}_{hours_before}h"
    already = (event.get("reminders_sent") or {}).get(reminder_key)
    if already and not force:
        return {"error": "already_sent", "sent_at": already.get("sent_at")}, 409

    now_dt = datetime.now(timezone.utc)
    deadline_dt = datetime.fromisoformat(deadline_iso)
    due_at = deadline_dt - timedelta(hours=hours_before)
    due_window_end = due_at + timedelta(hours=1)
    if only_if_due and not (due_at <= now_dt < due_window_end):
        return {"success": True, "kind": kind, "hours_before": hours_before, "skipped": "not_due", "simulated": _notifications_disabled()}, 200

    db = get_db()
    simulated = _notifications_disabled()
    notified = []
    skipped = []
    for doc in db.collection("teams").where("hackathon_event_id", "==", event_id).stream():
        team = doc.to_dict() or {}
        team_id = doc.id
        if team.get("active") is False:
            skipped.append({"team_id": team_id, "reason": "inactive"})
            continue
        slack_channel = team.get("slack_channel")
        if not slack_channel:
            skipped.append({"team_id": team_id, "reason": "no_slack_channel"})
            continue
        message = build_reminder_message(team, event, deadline_iso, hours_before)
        if message is None:
            skipped.append({"team_id": team_id, "reason": "already_done"})
            continue
        if not simulated:
            try:
                send_slack(message=message, channel=slack_channel)
            except Exception as e:
                logger.warning("send_deadline_reminders: send_slack failed for team %s: %s", team_id, e)
                skipped.append({"team_id": team_id, "reason": "send_failed"})
                continue
        notified.append(team_id)

    event_doc_id = event.get("id") or event_id
    db.collection("hackathons").document(event_doc_id).set(
        {"reminders_sent": {reminder_key: {
            "sent_at": now_dt.isoformat(),
            "deadline": deadline_iso,
            "teams_notified": notified,
            "by": actor,
        }}},
        merge=True,
    )
    clear_cache()
    send_slack_audit(
        action="deadline_reminder",
        message=f"Sent {kind} {hours_before}h reminders for {event_id}: {len(notified)} teams notified, {len(skipped)} skipped",
        payload={"event_id": event_id, "kind": kind, "hours_before": hours_before, "by": actor},
    )

    return {
        "success": True,
        "kind": kind,
        "hours_before": hours_before,
        "deadline": deadline_iso,
        "teams_total": len(notified) + len(skipped),
        "notified": notified,
        "skipped": skipped,
        "simulated": simulated,
    }, 200


def send_due_reminders_for_current_events():
    """Hourly cron entry point: every currently-running event x every
    reminder hour, only_if_due=True so most calls are no-ops."""
    from services.hackathons_service import get_hackathon_list

    events = (get_hackathon_list("current") or {}).get("hackathons") or []
    results = []
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            continue
        for hours_before in sorted(REMINDER_HOURS, reverse=True):
            payload, status = send_deadline_reminders(event_id, "submission", hours_before, only_if_due=True, actor="cron")
            results.append({"event_id": event_id, "hours_before": hours_before, "status": status, "result": payload})
    return {"success": True, "results": results}, 200
