from model.problem_statement import ProblemStatement
from model.user import User
from services import users_service
from services import problem_statements_service as service
from common.utils import safe_get_env_var
from common.auth import auth, auth_user
import logging

from flask import (
    Blueprint,
    request,
    g
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

    logger.info("add_problem_statement")
    res: ProblemStatement | None = service.save_problem_statement(request.get_json())
    if res is not None:
        return res.serialize()
    else:
        return None

@bp.route("", methods=["GET"])
def get_problem_statments():    
    result = []
    temp = service.get_problem_statements()
    for p in temp:
        result.append(p.serialize())

    print(f"get_problem_statments {result}")
    return {
        "problem_statements": result
    }

@bp.route("/<id>", methods=["GET"])
def get_single_problem(id):
    res: ProblemStatement | None = service.get_problem_statment_by_id(id)
    if res is not None:
        return res.serialize()
    else:
        #TODO: proper 404 handling: https://flask.palletsprojects.com/en/2.1.x/errorhandling/#custom-error-pages
        return None


@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
@bp.route("/events", methods=["PATCH"])
def update_problem_statement_events_link():    
    res = service.link_problem_statements_to_events(request.get_json())

    if res is not None:
        ps = []
        for p in res:
            ps.append(p.serialize())
        return ps
    else:
        #TODO: proper 404 handling: https://flask.palletsprojects.com/en/2.1.x/errorhandling/#custom-error-pages
        return None 
