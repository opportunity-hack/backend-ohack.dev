import logging
from flask import (
    Blueprint,
    request
)
from common.auth import auth, auth_user
from api.teams.teams_service import (
    queue_team,
    approve_team,
    get_queued_teams,
    edit_team,
    add_team_member,
    remove_team_member,
    remove_team,
    get_teams_by_hackathon_id,
    public_hackathon_teams_view,
    get_my_teams_by_event_id,
    send_team_message,
    toggle_completion_item,
    mark_team_complete,
)
from api.teams.gateway_keys import (
    get_team_gateway_key,
    list_gateway_key_statuses,
    MAX_STATUS_BATCH,
    provision_team_gateway_key,
    rotate_team_gateway_key,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bp_name = 'api-teams'
bp_url_prefix = '/api/team'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

@bp.route("/<hackathon_id>", methods=["GET"])
@auth.require_user
def get_teams_by_hackathon_id_api(hackathon_id):
    """
    Get all teams for a specific hackathon ID.
    """
    logger.info(f"GET /team/{hackathon_id} called")
    if auth_user and auth_user.user_id:
        payload = get_teams_by_hackathon_id(hackathon_id)
        # Admins (TeamManagement, judging admin) keep the full payload; anyone
        # else gets team internals stripped and slim member profiles.
        from services import hackathon_planning_service
        if hackathon_planning_service.is_admin(auth_user):
            return payload
        return public_hackathon_teams_view(payload)
    
    logger.error("Could not obtain user details for GET /team/<hackathon_id>")
    return {"error": "Unauthorized"}, 401

@bp.route("/<event_id>/me", methods=["GET"])
@auth.require_user
def get_my_teams_by_event_if_api(event_id):
    """
    Get teams for user with hackathon event id.
    """
    logger.info(f"GET /team/{event_id}/me called")
    if auth_user and auth_user.user_id:
        return get_my_teams_by_event_id(auth_user.user_id, event_id)
    
    logger.error("Could not obtain user details for GET /team/<event_id>/me")
    return {"error": "Unauthorized"}, 401

@bp.route("/edit", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def edit_team_api():
    """
    Admin endpoint to edit a team.
    Requires user to be an org member with volunteer.admin permission.
    """
    logger.info("PATCH /team/edit called")
    logger.info("Editing team")

    if auth_user and auth_user.user_id:
        return edit_team(request.get_json())
    
    logger.error("Could not obtain user details for PATCH /team/edit")
    return {"error": "Unauthorized"}, 401


@bp.route("/<teamid>/devpost", methods=["POST"])
@auth.require_user
def add_devpost_to_team_api(teamid):
    """
    Add a Devpost link to a team. Self-serve — the caller must be on the team
    (or an admin). Part 9 bug #1 fix: this used to call edit_team directly
    with NO membership check, so any logged-in user could overwrite any
    team's Devpost link. Routed through submissions.self_serve_team_edit,
    which also 409s once the event's submission window has closed for a
    non-admin caller. Lazy import: api.teams.teams_service must never import
    api.submissions (one-directional dependency).
    """
    logger.info(f"POST /team/{teamid}/devpost called")
    if auth_user and auth_user.user_id:
        logger.info(f"Adding Devpost link to team {teamid}")
        devpost_link = (request.get_json() or {}).get("devpost_link")
        logger.info(f"Devpost link: {devpost_link}")
        if not devpost_link:
            return {"error": "Devpost link is required"}, 400

        from services.hackathon_planning_service import is_admin
        from api.submissions.submissions_service import self_serve_team_edit
        return self_serve_team_edit(auth_user.user_id, teamid, {"devpost_link": devpost_link}, admin=is_admin(auth_user))

    logger.error("Could not obtain user details for POST /team/<teamid>/devpost")
    return {"error": "Unauthorized"}, 401

@bp.route("/<teamid>/demo-video", methods=["POST"])
@auth.require_user
def add_demo_video_to_team_api(teamid):
    """
    Add or clear a demo video URL for a team. Self-serve — the caller must be
    on the team (or an admin); see add_devpost_to_team_api's docstring for the
    Part 9 bug #1 context this fixes too.
    Pass demo_video_url as empty string or null to clear.
    """
    logger.info(f"POST /team/{teamid}/demo-video called")
    if auth_user and auth_user.user_id:
        body = request.get_json() or {}
        demo_video_url = body.get("demo_video_url", "")
        # Allow empty string to clear the field; edit_team normalizes to None

        from services.hackathon_planning_service import is_admin
        from api.submissions.submissions_service import self_serve_team_edit
        return self_serve_team_edit(auth_user.user_id, teamid, {"demo_video_url": demo_video_url}, admin=is_admin(auth_user))

    logger.error("Could not obtain user details for POST /team/<teamid>/demo-video")
    return {"error": "Unauthorized"}, 401

@bp.route("/<teamid>/completion/toggle", methods=["POST"])
@auth.require_user
def toggle_completion_item_api(teamid):
    """
    Mark a single Definition-of-Done item complete on a team. Self-serve for
    team members. Posts a Slack message into the team's channel. Re-toggling an
    already-done item returns 409 (no unchecking, no double-Slack).
    Body: { "item": "<slug>" }
    """
    logger.info(f"POST /team/{teamid}/completion/toggle called")
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    body = request.get_json() or {}
    item_slug = body.get("item")
    if not item_slug:
        return {"error": "Missing 'item' in request body"}, 400
    return toggle_completion_item(auth_user.user_id, teamid, item_slug)


@bp.route("/<teamid>/completion/complete", methods=["POST"])
@auth.require_user
def mark_team_complete_api(teamid):
    """
    Mark a team's project COMPLETE. Self-serve for team members. Requires all
    8 checklist items to be done first. Posts a celebration message to the
    team's Slack channel CCing the OHack admins.
    """
    logger.info(f"POST /team/{teamid}/completion/complete called")
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    return mark_team_complete(auth_user.user_id, teamid)


@bp.route("/<teamid>/member", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_member_to_team_api(teamid):
    """
    Admin endpoint to add a member to a team.
    Requires user to be an org member with volunteer.admin permission.
    """
    logger.info(f"POST /team/{teamid}/member called")
    if auth_user and auth_user.user_id:
        # Get the user_id from the request
        user_id = request.get_json().get("id")
        return add_team_member(teamid, user_id)
    
    logger.error("Could not obtain user details for POST /team/<teamid>/member")
    return {"error": "Unauthorized"}, 401

@bp.route("/<teamid>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def delete_team_api(teamid):
    """
    Admin endpoint to delete a team.
    Requires user to be an org member with volunteer.admin permission.
    """
    logger.info(f"DELETE /team/{teamid} called")
    if auth_user and auth_user.user_id:
        return remove_team(teamid)
    
    logger.error("Could not obtain user details for DELETE /team/<teamid>")
    return {"error": "Unauthorized"}, 401


@bp.route("/<teamid>/member", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def remove_member_from_team_api(teamid):
    """
    Admin endpoint to remove a member from a team.
    Requires user to be an org member with volunteer.admin permission.
    """
    logger.info(f"DELETE /team/{teamid}/member called")
    if auth_user and auth_user.user_id:
        # Get the user_id from the request
        user_id = request.get_json().get("id")
        return remove_team_member(teamid, user_id)
    
    logger.error("Could not obtain user details for DELETE /team/<teamid>/member")
    return {"error": "Unauthorized"}, 401

@bp.route("/queue", methods=["POST"])
@auth.require_user
def add_team_to_queue():
    """
    Queue a team for assignment to a nonprofit.
    Team will be saved with status IN_REVIEW and active=False.
    Team members will be notified via Slack about the queue status.
    """
    logger.info("POST /team/queue called")
    if auth_user and auth_user.user_id:
        return queue_team(auth_user.user_id, request.get_json())
    
    logger.error("Could not obtain user details for POST /team/queue")
    return {"error": "Unauthorized"}, 401

@bp.route("/approve", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def approve_team_assignment():
    """
    Admin endpoint to approve a team and assign it to a nonprofit.
    Sets status to APPROVED, active=True, creates GitHub repo,
    and sends notification to the team.
    """
    logger.info("POST /team/approve called")
    if auth_user and auth_user.user_id:
        return approve_team(auth_user.user_id, request.get_json())
    
    logger.error("Could not obtain user details for POST /team/approve")
    return {"error": "Unauthorized"}, 401

@bp.route("/<teamid>/gateway-key", methods=["GET"])
@auth.require_user
def get_gateway_key_api(teamid):
    """
    Return the team's AI gateway API key. Team members only (or admins).
    The plaintext key is only ever served to team members and admins.
    """
    logger.info(f"GET /team/{teamid}/gateway-key called")
    if auth_user and auth_user.user_id:
        from services.hackathon_planning_service import is_admin
        return get_team_gateway_key(
            auth_user.user_id, teamid, is_admin=is_admin(auth_user)
        )

    logger.error("Could not obtain user details for GET /team/<teamid>/gateway-key")
    return {"error": "Unauthorized"}, 401


@bp.route("/<teamid>/gateway-key/rotate", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def rotate_gateway_key_api(teamid):
    """
    Admin endpoint to rotate a team's AI gateway key (leak recovery).
    Deletes the old key in LiteLLM and mints a fresh one under the same alias.
    """
    logger.info(f"POST /team/{teamid}/gateway-key/rotate called")
    if auth_user and auth_user.user_id:
        try:
            return rotate_team_gateway_key(teamid), 200
        except RuntimeError as e:
            logger.warning(f"Gateway key rotate refused for team {teamid}: {e}")
            return {"error": str(e)}, 404
        except Exception as e:
            logger.error(f"Gateway key rotate failed for team {teamid}: {e}")
            return {"error": f"rotation failed: {e}"}, 502

    logger.error("Could not obtain user details for POST /team/<teamid>/gateway-key/rotate")
    return {"error": "Unauthorized"}, 401


@bp.route("/<teamid>/gateway-key/retry", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def retry_gateway_key_api(teamid):
    """
    Admin endpoint to (re)provision a team's AI gateway key. Idempotent:
    returns the active key's metadata when one already exists. Covers the
    case where approve_team's best-effort mint failed.
    """
    logger.info(f"POST /team/{teamid}/gateway-key/retry called")
    if auth_user and auth_user.user_id:
        try:
            return provision_team_gateway_key(teamid), 200
        except Exception as e:
            logger.error(f"Gateway key provision failed for team {teamid}: {e}")
            return {"error": f"provisioning failed: {e}"}, 502

    logger.error("Could not obtain user details for POST /team/<teamid>/gateway-key/retry")
    return {"error": "Unauthorized"}, 401


@bp.route("/admin/gateway-keys", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def list_gateway_key_statuses_api():
    """
    Admin: AI gateway key status for many teams in one call
    (?team_ids=a,b,c — the admin Teams table already holds the ids).
    Metadata only; the plaintext is only served by GET /<teamid>/gateway-key.
    """
    raw = request.args.get("team_ids", "")
    team_ids = [t.strip() for t in raw.split(",") if t.strip()]
    if not team_ids:
        return {"error": "team_ids_required"}, 400
    if len(team_ids) > MAX_STATUS_BATCH:
        return {"error": "too_many_team_ids", "max": MAX_STATUS_BATCH}, 400
    return {"keys": list_gateway_key_statuses(team_ids)}, 200


@bp.route("/admin/<teamid>", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_team_admin_api(teamid):
    """Admin-only full team doc (admin_notes, nonprofit_rankings, comments,
    communication_history) — the public team routes strip those."""
    from services import teams_service as services_teams
    team = services_teams.get_team_admin(teamid)
    if not team:
        return {"error": "not_found"}, 404
    return {"team": team}

@bp.route("/admin/<teamid>/message", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def send_team_message_api(teamid):
    """
    Admin endpoint to send a message to a team.
    Requires user to be an org member with volunteer.admin permission.
    """
    logger.info(f"POST /team/admin/{teamid}/message called")
    if auth_user and auth_user.user_id:
        return send_team_message(auth_user, teamid, request.get_json())
    
    logger.error("Could not obtain user details for POST /team/admin/message")
    return {"error": "Unauthorized"}, 401

@bp.route("/queue", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_queued_teams_api():
    """
    Admin endpoint to get all teams in the queue (status IN_REVIEW)
    """
    logger.info("GET /team/queue called")
    return get_queued_teams()