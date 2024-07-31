from model.user import User
from services import hearts_service
from common.utils import safe_get_env_var
from common.auth import auth, auth_user

from flask import (
    Blueprint,
    request,
    g
)

bp_name = 'api-hearts'
bp_url_prefix = '/api'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


# Used to provide profile details - user must be logged in
@bp.route("/hearts", methods=["GET"])
@auth.require_user
def get_hearts():         
    print("get_hearts")   
    res = hearts_service.get_hearts_for_all_users()
    print(f"res: {res}")
    return {"hearts": res}

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")
    
@bp.route("/hearts", methods=["POST"])
@auth.require_org_member_with_permission("heart.admin", req_to_org_id=getOrgId)
def save_hearts():        
    print("save_hearts")
    if auth_user and auth_user.user_id: 
        print(f"request.get_json(): {request.get_json()}")        
        res = hearts_service.save_hearts(auth_user.user_id, request.get_json())
        return {"hearts": res}
    else:
        return "Error: Could not obtain user details for POST /hearts"
