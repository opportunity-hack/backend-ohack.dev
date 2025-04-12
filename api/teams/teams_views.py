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
    get_teams_by_hackathon_id
)

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)

bp_name = 'api-teams'
bp_url_prefix = '/api/team'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

def get_org_id(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

@bp.route("/<hackathon_id>", methods=["GET"])
@auth.require_user
def get_teams_by_hackathon_id_api(hackathon_id):
    """
    Get all teams for a specific hackathon ID.
    """
    if auth_user and auth_user.user_id:
        return get_teams_by_hackathon_id(auth_user.user_id, hackathon_id)
    
    logger.error("Could not obtain user details for GET /team/<hackathon_id>")
    return {"error": "Unauthorized"}, 401


@bp.route("/queue", methods=["POST"])
@auth.require_user
def add_team_to_queue():
    """
    Queue a team for assignment to a nonprofit.
    Team will be saved with status IN_REVIEW and active=False.
    Team members will be notified via Slack about the queue status.
    """
    if auth_user and auth_user.user_id:
        return queue_team(auth_user.user_id, request.get_json())
    
    logger.error("Could not obtain user details for POST /team/queue")
    return {"error": "Unauthorized"}, 401

@bp.route("/approve", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=get_org_id)
def approve_team_assignment():
    """
    Admin endpoint to approve a team and assign it to a nonprofit.
    Sets status to APPROVED, active=True, creates GitHub repo,
    and sends notification to the team.
    """
    if auth_user and auth_user.user_id:
        return approve_team(auth_user.user_id, request.get_json())
    
    logger.error("Could not obtain user details for POST /team/approve")
    return {"error": "Unauthorized"}, 401

@bp.route("/queue", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=get_org_id)
def get_queued_teams_api():
    """
    Admin endpoint to get all teams in the queue (status IN_REVIEW)
    """
    return get_queued_teams()