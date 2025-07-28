from flask import Blueprint, request
from common.auth import auth, auth_user, getOrgId
from common.log import get_logger, debug, error

from api.judging.judging_service import (
    get_judge_assignments,
    get_judge_teams,
    get_team_details,
    submit_judge_score,
    get_judge_scores,
    save_draft_score,
    is_judge_assigned_to_team,
    create_judge_assignment,
    update_judge_assignment_details,
    remove_judge_assignment,
    get_individual_judge_score,
    get_event_judge_panels,
    create_judge_panel,
    update_judge_panel_details,
    remove_judge_panel,
    get_judge_assignments_for_panel,
    get_judge_event_details,
    get_round2_average_scores,
)

logger = get_logger("judging_views")

BP_NAME = 'api-judging'
BP_URL_PREFIX = '/api/judge'
bp = Blueprint(BP_NAME, __name__, url_prefix=BP_URL_PREFIX)


def get_authenticated_user_id():
    """Helper function to get authenticated user ID with proper error handling"""
    if not auth_user or not auth_user.user_id:
        error(logger, "Could not obtain user details - user not authenticated")
        return None
    return auth_user.user_id


@bp.route("/assignments/<judge_id>", methods=["GET"])
@auth.require_user
def get_assignments(judge_id):
    """Get all hackathon assignments for a specific judge."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Verify judge can only access their own assignments
    if user_id != judge_id:
        error(logger, "Judge attempting to access another judge's assignments",
              user_id=user_id, requested_judge_id=judge_id)
        return {"error": "Forbidden"}, 403

    debug(logger, "Getting judge assignments", judge_id=judge_id)
    result = get_judge_assignments(judge_id)

    if "error" in result:
        return result, 500

    return result



@bp.route("/teams/<judge_id>/<event_id>", methods=["GET"])
@auth.require_user
def get_teams(judge_id, event_id):
    """Get teams assigned to a judge for a specific hackathon."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Verify judge can only access their own teams
    if user_id != judge_id:
        error(logger, "Judge attempting to access another judge's teams",
              user_id=user_id, requested_judge_id=judge_id)
        return {"error": "Forbidden"}, 403

    debug(logger, "Getting judge teams",
          judge_id=judge_id, event_id=event_id)
    result = get_judge_teams(judge_id, event_id)

    if "error" in result:
        return result, 500

    logger.debug(f"Result: {result}")
    return result


@bp.route("/team/<team_id>", methods=["GET"])
@auth.require_user
def get_team_api(team_id):
    """Get detailed information about a specific team for judging."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Verify judge is assigned to this team
    if not is_judge_assigned_to_team(user_id, team_id):
        error(logger, "Judge attempting to access unassigned team",
              judge_id=user_id, team_id=team_id)
        return {"error": "Forbidden: Judge not assigned to this team"}, 403

    debug(logger, "Getting team details",
          team_id=team_id, judge_id=user_id)
    result = get_team_details(team_id)

    if "error" in result:
        return result, 404 if result["error"] == "Team not found" else 500

    logger.debug(f"Result: {result}")
    return result


@bp.route("/score", methods=["POST"])
@auth.require_user
def submit_score():
    """Submit or update a team score."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400
    
    logger.debug(f"Judging submit_score Data: {data}")

    # Validate required fields
    required_fields = ['judge_id', 'team_id', 'event_id', 'round', 'scores']
    for field in required_fields:
        if field not in data:
            return {"error": f"Missing required field: {field}"}, 400

    # Verify judge can only submit scores for themselves
    if user_id != data['judge_id']:
        error(logger, "Judge attempting to submit score for another judge",
              user_id=user_id, requested_judge_id=data['judge_id'])
        return {"error": "Forbidden"}, 403

    # Verify judge is assigned to this team
    if not is_judge_assigned_to_team(user_id, data['team_id']):
        error(logger, "Judge attempting to score unassigned team",
              judge_id=user_id, team_id=data['team_id'])
        return {"error": "Forbidden: Judge not assigned to this team"}, 403

    debug(logger, "Submitting score", judge_id=data['judge_id'],
          team_id=data['team_id'], event_id=data['event_id'])

    result = submit_judge_score(
        judge_id=data['judge_id'],
        team_id=data['team_id'],
        event_id=data['event_id'],
        round_name=data['round'],
        scores_data=data['scores'],
        submitted_at=data.get('submitted_at'),
        feedback=data.get('feedback', '')
    )

    if not result.get('success', False):
        return result, 400

    return result

@bp.route("/judge/<judge_id>/event/<event_id>", methods=["GET"])
@auth.require_user
def get_judge_event_api(judge_id, event_id):
    """Get all information about a specific judge's event."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401


    debug(logger, "Getting judge event",
          judge_id=judge_id, event_id=event_id)
    result = get_judge_event_details(judge_id, event_id)

    if "error" in result:
        return result, 500

    return result


@bp.route("/scores/<judge_id>/<event_id>", methods=["GET"])
@auth.require_user
def get_scores(judge_id, event_id):
    """Get all scores submitted by a judge for a hackathon."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Verify judge can only access their own scores
    if user_id != judge_id:
        error(logger, "Judge attempting to access another judge's scores",
              user_id=user_id, requested_judge_id=judge_id)
        return {"error": "Forbidden"}, 403

    debug(logger, "Getting judge scores",
          judge_id=judge_id, event_id=event_id)
    result = get_judge_scores(judge_id, event_id)

    if "error" in result:
        return result, 500

    return result


@bp.route("/draft", methods=["POST"])
@auth.require_user
def save_draft():
    """Save draft scores (auto-save functionality)."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400

    logger.debug(f"Data: {data}")

    # Validate required fields
    required_fields = ['judge_id', 'team_id', 'event_id', 'round', 'scores']
    for field in required_fields:
        if field not in data:
            return {"error": f"Missing required field: {field}"}, 400

    # Verify judge can only save drafts for themselves
    if user_id != data['judge_id']:
        error(logger, "Judge attempting to save draft for another judge",
              user_id=user_id, requested_judge_id=data['judge_id'])
        return {"error": "Forbidden"}, 403

    # Verify judge is assigned to this team
    if not is_judge_assigned_to_team(user_id, data['team_id']):
        error(logger, "Judge attempting to save draft for unassigned team",
              judge_id=user_id, team_id=data['team_id'])
        return {"error": "Forbidden: Judge not assigned to this team"}, 403

    debug(logger, "Saving draft score", judge_id=data['judge_id'],
          team_id=data['team_id'], event_id=data['event_id'])

    result = save_draft_score(
        judge_id=data['judge_id'],
        team_id=data['team_id'],
        event_id=data['event_id'],
        round_name=data['round'],
        scores_data=data['scores'],
        updated_at=data.get('updated_at'),
        feedback=data.get('feedback', '')
    )

    if not result.get('success', False):
        return result, 400

    return result


# Judge Assignment Management Endpoints
@bp.route("/panel/<panel_id>/assignments", methods=["GET"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
@auth.require_user
def get_panel_assignments(panel_id):
    """Get all judge assignments for a specific panel."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    logger.debug(f"Getting judge assignments for panel {panel_id}")
    result = get_judge_assignments_for_panel(panel_id)
    logger.debug(f"Result: {result}")
   

    return result


@bp.route("/assignments", methods=["POST"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def create_assignment():
    """Create a new judge assignment."""
    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400

    required_fields = ['judge_id', 'event_id', 'team_id', 'round']
    for field in required_fields:
        if field not in data:
            return {"error": f"Missing required field: {field}"}, 400

    logger.debug(f"Creating judge assignment with data: {data}")

    result = create_judge_assignment(
        judge_id=data['judge_id'],
        event_id=data['event_id'],
        team_id=data['team_id'],
        round_name=data['round'],
        panel_id=data.get('panel_id'),
        demo_time=data.get('demo_time'),
        room=data.get('room')
    )

    if not result.get('success', False):
        return result, 400

    return result, 201


@bp.route("/assignments/<assignment_id>", methods=["PUT"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def update_assignment(assignment_id):
    """Update judge assignment details."""
    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400

    debug(logger, "Updating judge assignment", assignment_id=assignment_id)

    result = update_judge_assignment_details(
        assignment_id=assignment_id,
        demo_time=data.get('demo_time'),
        room=data.get('room')
    )

    if not result.get('success', False):
        return result, 400

    return result


@bp.route("/assignments/<assignment_id>", methods=["DELETE"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def delete_assignment(assignment_id):
    """Remove a judge assignment."""
    debug(logger, "Removing judge assignment", assignment_id=assignment_id)

    result = remove_judge_assignment(assignment_id)

    if not result.get('success', False):
        return result, 400

    return result


# Individual Judge Score Endpoint

@bp.route("/score/<judge_id>/<team_id>/<event_id>/<round_name>", methods=["GET"])
@auth.require_user
def get_individual_score(judge_id, team_id, event_id, round_name):
    """Get a specific judge score."""
    user_id = get_authenticated_user_id()
    if not user_id:
        return {"error": "Unauthorized"}, 401

    # Verify judge can only access their own scores
    if user_id != judge_id:
        error(logger, "Judge attempting to access another judge's score",
              user_id=user_id, requested_judge_id=judge_id)
        return {"error": "Forbidden"}, 403

    # Verify judge is assigned to this team
    if not is_judge_assigned_to_team(user_id, team_id):
        error(logger, "Judge attempting to access score for unassigned team",
              judge_id=user_id, team_id=team_id)
        return {"error": "Forbidden: Judge not assigned to this team"}, 403

    is_draft = request.args.get('draft', 'false').lower() == 'true'

    debug(logger, "Getting individual judge score",
          judge_id=judge_id, team_id=team_id, event_id=event_id,
          round_name=round_name, is_draft=is_draft)

    result = get_individual_judge_score(judge_id, team_id, event_id, round_name, is_draft)

    if "error" in result:
        return result, 500

    return result


# Judge Panel Management Endpoints

@bp.route("/panels/<event_id>", methods=["GET"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def get_panels(event_id):
    """Get all judge panels for an event."""
    debug(logger, "Getting judge panels for event", event_id=event_id)

    result = get_event_judge_panels(event_id)

    if "error" in result:
        return result, 500

    return result


@bp.route("/panels", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def create_panel():
    """Create a new judge panel."""
    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400

    required_fields = ['event_id', 'panel_name', 'room']
    for field in required_fields:
        if field not in data:
            return {"error": f"Missing required field: {field}"}, 400

    debug(logger, "Creating judge panel",
          event_id=data['event_id'], panel_name=data['panel_name'],
          room=data['room'])

    result = create_judge_panel(
        event_id=data['event_id'],
        panel_name=data['panel_name'],
        room=data['room'],
        panel_id = data.get('panel_id', None)
    )

    if not result.get('success', False):
        return result, 400

    return result, 201


@bp.route("/panels/<panel_id>", methods=["PUT"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def update_panel(panel_id):
    """Update judge panel details."""
    data = request.get_json()
    if not data:
        return {"error": "Missing request body"}, 400    

    logger.debug(f"Updating judge panel {panel_id} with data: {data}")

    result = update_judge_panel_details(
        panel_id=panel_id,
        panel_name=data.get('panel_name'),
        room=data.get('room')        
    )

    if not result.get('success', False):
        return result, 400

    return result


@bp.route("/panels/<panel_id>", methods=["DELETE"])
@auth.require_org_member_with_permission("judge.admin", req_to_org_id=getOrgId)
def delete_panel(panel_id):
    """Remove a judge panel."""
    debug(logger, "Removing judge panel", panel_id=panel_id)

    result = remove_judge_panel(panel_id)

    if not result.get('success', False):
        return result, 400

    return result