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


# Used to provide profile details - user must be logged in
@bp.route("/profile", methods=["GET"])
@auth.require_user
def profile():            
    # user_id is a uuid from Propel Auth
    if auth_user and auth_user.user_id:        
        return vars(users_service.get_profile_metadata(auth_user.user_id))
    else:
        return None

@bp.route("/profile", methods=["POST"])
@auth.require_user
def save_profile():        
    if auth_user and auth_user.user_id:        
        return vars(users_service.save_profile_metadata(auth_user.user_id, request.get_json()))
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