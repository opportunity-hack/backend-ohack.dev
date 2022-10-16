from flask import (
    Blueprint,
    request,
    g
)

from api.messages.messages_service import (
    get_profile_metadata,
    get_public_message,
    get_protected_message,
    get_admin_message,
    save_npo,
    update_npo,
    remove_npo,
    get_npo_list,
    get_single_npo,
    save_problem_statement,
    get_problem_statement_list,
    save_hackathon,
    get_teams_list,
    save_team,
    get_hackathon_list,
    link_problem_statements_to_events,
    save_helping_status
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


#
# Nonprofit Related Endpoints

@bp.route("/npo", methods=["POST"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def add_npo(): 
    return vars(save_npo(request.get_json()))

@bp.route("/npo/edit", methods=["PATCH"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def edit_npo(): 
    return vars(update_npo(request.get_json()))

@bp.route("/npo", methods=["DELETE"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def delete_npo():        
    return vars(remove_npo(request.get_json()))


@bp.route("/npos", methods=["GET"])
def get_npos():
    return (get_npo_list())


@bp.route("/npo/<npo_id>", methods=["GET"])
def get_npo(npo_id):
    return (get_single_npo(npo_id))



#
# Hackathon Related Endpoints

@bp.route("/hackathon", methods=["POST"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def add_hackathon():
    return vars(save_hackathon(request.get_json()))


@bp.route("/hackathons", methods=["GET"])
def list_hackathons():
    is_current = request.args.get("current") != None
    return get_hackathon_list(is_current)

# Problem Statement (also called Project) Related Endpoints
@bp.route("/problem_statement", methods=["POST"])
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def add_problem_statement():
    return vars(save_problem_statement(request.get_json()))

@bp.route("/problem_statements", methods=["GET"])
def get_problem_statments():    
    return get_problem_statement_list()


@authorization_guard
@permissions_guard([admin_messages_permissions.read])
@bp.route("/problem_statements/events", methods=["PATCH"])
def update_problem_statement_events_link():    
    return vars(link_problem_statements_to_events(request.get_json()))

@bp.route("/teams", methods=["GET"])
def get_teams():
    return (get_teams_list())


@bp.route("/team", methods=["POST"])
def add_team():
    return vars(save_team(request.get_json()))

# Used to register when person says they are helping, not helping
@bp.route("/profile/helping", methods=["POST"])
@authorization_guard
def register_helping_status():
    return vars(save_helping_status(request.get_json()))

# Used to provide profile details - user must be logged in
@bp.route("/profile")
@authorization_guard
def profile():    
    # We should get the user information passed into the context,
    #  and not honored by the parameter
    #  that is, don't allow for someone to pass in a <user_id>
    #  but instead, look it up via auth token so this is secure
    token = g.get("access_token")
    if token:
        user_id = token["sub"]
        return vars(get_profile_metadata(user_id))
    else:
        return None


# Used to provide feedback details - user must be logged in
@bp.route("/feedback/<user_id>")
@authorization_guard
def feedback(user_id):
    # TODO: This is stubbed out, need to change with new function for get_feedback
    return vars(get_profile_metadata(user_id))
