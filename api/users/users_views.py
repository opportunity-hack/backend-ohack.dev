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


@bp.route("/profile/privacy-settings", methods=["GET"])
@auth.require_user
def get_privacy_settings():
    """Get user's privacy settings"""
    if auth_user and auth_user.user_id:
        privacy_settings = users_service.get_privacy_settings(auth_user.user_id)
        if privacy_settings is not None:
            return {"privacy_settings": privacy_settings}
        return {"privacy_settings": {}}
    return {"error": "Unauthorized"}, 401


@bp.route("/profile/privacy-settings", methods=["PATCH"])
@auth.require_user
def update_privacy_settings():
    """Update user's privacy settings"""
    if auth_user and auth_user.user_id:
        data = request.get_json()
        if not data:
            return {"error": "No data provided"}, 400

        result = users_service.update_privacy_settings(auth_user.user_id, data)
        if result:
            return {
                "privacy_settings": result,
                "message": "Privacy settings updated successfully"
            }
        return {"error": "Failed to update privacy settings"}, 500
    return {"error": "Unauthorized"}, 401


# Privacy-aware public profile endpoints
@bp.route("/<user_id>/profile/public", methods=["GET"])
def get_public_profile_by_db_id(user_id):
    """Get privacy-filtered public profile by database ID"""
    profile_data = users_service.get_privacy_filtered_profile_by_db_id(user_id)
    if profile_data:
        return profile_data
    return {"error": "User not found"}, 404


@bp.route("/<user_id>/profile/privacy-settings", methods=["GET"])
def get_public_privacy_settings_by_db_id(user_id):
    """Get public privacy settings by database ID (for frontend to know what's public)"""
    privacy_settings = users_service.get_public_privacy_settings_by_db_id(user_id)
    if privacy_settings is not None:
        return {"privacy_settings": privacy_settings}
    return {"error": "User not found"}, 404


@bp.route("/<user_id>/praises", methods=["GET"])
def get_received_praises_by_db_id(user_id):
    """Public, paginated list of praises received by a user.

    Query params: ?limit=&offset=. Returns 404 when the user is not found and
    403 when the user has hidden their praises.
    """
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = int(request.args.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0

    user = users_service.get_user_by_db_id(user_id)
    if user is None:
        return {"error": "User not found"}, 404

    result = users_service.get_received_praises_by_db_id(user_id, limit=limit, offset=offset)
    if result is None:
        return {"error": "Praises are private for this user"}, 403
    return result