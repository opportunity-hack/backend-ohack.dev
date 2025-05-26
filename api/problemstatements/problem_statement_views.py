from model.problem_statement import ProblemStatement
from model.user import User
from services import users_service
from services import problem_statements_service as service
from common.utils import safe_get_env_var
from common.auth import auth, auth_user
from common.exceptions import InvalidInputError
import logging

from flask import (
    Blueprint,
    request,
    g,
    jsonify
)

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)


bp_name = 'api-problem-statements'
bp_url_prefix = '/api/problem-statements' #TODO: Breaking API change w/ frontend
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)
def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

# Problem Statement (also called Project) Related Endpoints
@bp.route("", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_problem_statement():
    try:
        result = service.save_problem_statement(request.get_json())
        return jsonify(result.serialize()), 201
    except InvalidInputError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error in add_problem_statement: {str(e)}")
        # Log stack trace for debugging
        logger.debug(e, exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@bp.route("", methods=["GET"])
def get_problem_statements():    
    try:
        results = service.get_problem_statements()
        return jsonify({
            "problem_statements": [p.serialize() for p in results]
        })
    except Exception as e:
        logger.error(f"Error in get_problem_statements: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@bp.route("/<id>", methods=["GET"])
def get_single_problem(id):
    try:
        result = service.get_problem_statement(id)
        if result is None:
            return jsonify({"error": "Problem statement not found"}), 404
            
        # Properly serialize the problem statement and its related objects
        serialized = result.serialize()
        
        # Handle linked hackathons if they exist, filtering out None values
        if hasattr(result, 'events') and result.events:
            serialized['events'] = [event.serialize() for event in result.events if event is not None]
            
        return jsonify(serialized)
    except Exception as e:
        logger.error(f"Error in get_single_problem: {str(e)}")
        logger.debug(f"Problem statement data: {result}")  # Add debug logging
        return jsonify({"error": "Internal server error"}), 500

@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
@bp.route("/events", methods=["PATCH"])
def update_problem_statement_events_link():    
    res = service.link_problem_statements_to_events(request.get_json())

    if res is not None:
        return jsonify(res.serialize()), 200
    else:
        #Handle as 404 
        return jsonify({"error": "Problem statement not found"}), 404        
