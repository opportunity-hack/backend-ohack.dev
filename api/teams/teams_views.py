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
    get_my_teams_by_event_id,
    send_team_message
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
        return get_teams_by_hackathon_id(hackathon_id)
    
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
    Add a Devpost link to a team.
    Requires user to be authenticated.
    """
    logger.info(f"POST /team/{teamid}/devpost called")
    if auth_user and auth_user.user_id:
        # Get the Devpost link from the request
        logger.info(f"Adding Devpost link to team {teamid}")
        devpost_link = request.get_json().get("devpost_link")
        logger.info(f"Devpost link: {devpost_link}")
        if not devpost_link:
            return {"error": "Devpost link is required"}, 400
        
        return edit_team({"id": teamid, "devpost_link": devpost_link})
    
    logger.error("Could not obtain user details for POST /team/<teamid>/devpost")
    return {"error": "Unauthorized"}, 401

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