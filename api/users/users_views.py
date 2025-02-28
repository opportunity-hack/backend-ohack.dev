from model.user import User
from services import users_service
from common.utils import safe_get_env_var
from common.auth import auth, auth_user

from flask import (
    Blueprint,
    request,
    g
)

bp_name = 'api-users'
bp_url_prefix = '/api/users' #TODO: Breaking API change w/ frontend
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")


# Used to provide profile details - user must be logged in
@bp.route("/profile", methods=["GET"])
@auth.require_user
def profile():            
    # user_id is a uuid from Propel Auth
    if auth_user and auth_user.user_id:  
        u: User | None = users_service.get_profile_metadata(auth_user.user_id)
        return vars(u) if u is not None else None
    else:
        return None

@bp.route("/profile", methods=["POST"])
@auth.require_user
def save_profile():        
    if auth_user and auth_user.user_id: 
        u: User | None = users_service.save_profile_metadata(auth_user.user_id, request.get_json())
        return vars(u) if u is not None else None
    else:
        return None


# Get user profile by user id
@bp.route("/<id>/profile", methods=["GET"])
def get_profile_by_db_id(id):
    p = users_service.get_profile_by_db_id(id)
    if p: 
        return p #Already a dict
    else: 
        return None
    

@bp.route("/volunteering", methods=["POST"])
@auth.require_user
def save_volunteering_time():        
    if auth_user and auth_user.user_id: 
        u: User | None = users_service.save_volunteering_time(auth_user.user_id, request.get_json())
        return vars(u) if u is not None else None
    else:
        return None
    

@bp.route("/volunteering", methods=["GET"])
@auth.require_user
def get_volunteering_time():        
    # Get url params
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')
    
    if auth_user and auth_user.user_id:
        allVolunteering, totalActiveHours, totalCommitmentHours = users_service.get_volunteering_time(auth_user.user_id, start_date, end_date)
        return {
            "totalActiveHours": totalActiveHours,
            "totalCommitmentHours": totalCommitmentHours,
            "allVolunteering": allVolunteering            
        }
    else:
        return None
    
    
@bp.route("/admin/volunteering", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_all_volunteering_time():        
    # Get url params
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')
    
    if auth_user and auth_user.user_id:
        allVolunteering, totalActiveHours, totalCommitmentHours = users_service.get_all_volunteering_time(start_date, end_date)
        return {
            "totalActiveHours": totalActiveHours,
            "totalCommitmentHours": totalCommitmentHours,
            "volunteerSessions": allVolunteering            
        }
    else:
        return None