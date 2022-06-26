from flask import (
    Blueprint,
    request
)

from api.messages.messages_service import (
    get_profile_metadata,
    get_public_message,
    get_protected_message,
    get_admin_message,
    save_npo
)
from api.security.guards import (
    authorization_guard,
    permissions_guard,
    admin_messages_permissions
)

bp_name = 'api-messages'
bp_url_prefix = '/api/messages'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


@bp.route("/public")
def public():
    return vars(get_public_message())


@bp.route("/protected")
@authorization_guard
def protected():
    return vars(get_protected_message())


@bp.route("/admin")
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def admin():    
    return vars(get_admin_message())


@bp.route("/npo", methods=["POST"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def add_npo():    
    return vars(save_npo(request.get_json()))


# Used to provide profile details - user must be logged in
@bp.route("/profile/<user_id>")
@authorization_guard
def profile(user_id):
    return vars(get_profile_metadata(user_id))


# Used to provide feedback details - user must be logged in
@bp.route("/feedback/<user_id>")
@authorization_guard
def feedback(user_id):
    # TODO: This is stubbed out, need to change with new function for get_feedback
    return vars(get_profile_metadata(user_id))
