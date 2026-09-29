"""
Flask routes for team project write-ups + submission deadlines.

Membership + deadline enforcement live in submissions_service (
_authorize_team_write); these views only extract the request body and check
admin status via is_admin(auth_user) for the bypass.
"""
import logging
from flask import Blueprint, request

from common.auth import auth, auth_user
from services.hackathon_planning_service import is_admin
from common.utils.api_key import check_api_key
from api.submissions.submissions_service import (
    save_project,
    submit_project,
    set_mentor_help_wanted,
    get_submission_window_for_event,
    send_deadline_reminders,
    send_due_reminders_for_current_events,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bp_name = "api-submissions"
bp = Blueprint(bp_name, __name__, url_prefix="/api")


def _unauthorized():
    return {"error": "Unauthorized"}, 401


@bp.route("/team/<teamid>/project", methods=["POST"])
@auth.require_user
def save_project_api(teamid):
    """Partial update of a team's project write-up fields.
    Body: any of project_tagline, project_story, project_built_with,
    project_links, project_thumbnail_url, project_images."""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    payload = request.get_json() or {}
    return save_project(auth_user.user_id, teamid, payload, admin=is_admin(auth_user))


@bp.route("/team/<teamid>/project/submit", methods=["POST"])
@auth.require_user
def submit_project_api(teamid):
    """Marks the team's project submitted (or late, inside the grace window)."""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    return submit_project(auth_user.user_id, teamid, admin=is_admin(auth_user))


@bp.route("/team/<teamid>/mentor-availability", methods=["POST"])
@auth.require_user
def set_mentor_availability_api(teamid):
    """Body: { open: bool } — team-facing 'open to mentors / heads-down' signal."""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    return set_mentor_help_wanted(auth_user.user_id, teamid, body.get("open"), admin=is_admin(auth_user))


@bp.route("/hackathons/<event_id>/submissions/window", methods=["GET"])
def submissions_window_api(event_id):
    """Public — the dashboard's deadline strip and the DeadlinesSection admin
    preview both read this so the countdown is driven by the server clock."""
    return get_submission_window_for_event(event_id)


@bp.route("/hackathons/<event_id>/deadlines/remind", methods=["POST"])
@auth.optional_user
def remind_api(event_id):
    """Admin button (Bearer token) OR the hourly cron (X-Api-Key:
    BACKEND_CRON_TOKEN) — @auth.optional_user populates auth_user when a
    valid token is present but doesn't 401 when it's absent, so the API-key
    branch can still be reached."""
    is_admin_caller = bool(auth_user and getattr(auth_user, "user_id", None) and is_admin(auth_user))
    if not is_admin_caller and not check_api_key(request, "BACKEND_CRON_TOKEN"):
        return {"error": "Forbidden"}, 403

    body = request.get_json() or {}
    kind = body.get("kind", "submission")
    hours_before = body.get("hours_before")
    only_if_due = bool(body.get("only_if_due", False))
    force = bool(body.get("force", False))
    actor = auth_user.user_id if is_admin_caller else "cron"
    return send_deadline_reminders(event_id, kind, hours_before, only_if_due=only_if_due, force=force, actor=actor)


@bp.route("/hackathons/deadlines/remind-due", methods=["POST"])
def remind_due_api():
    """Hourly GitHub Actions cron — API key only, no admin bypass (nothing to
    bypass: it always iterates every currently-running event)."""
    if not check_api_key(request, "BACKEND_CRON_TOKEN"):
        return {"error": "Forbidden"}, 403
    return send_due_reminders_for_current_events()
