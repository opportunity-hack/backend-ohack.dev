import os 

from common.auth import auth, auth_user

from flask import (
    Blueprint,
    request,
    g
)

from api.messages.messages_service import (
    get_problem_statement_list_old,
    get_profile_metadata_old,
    get_public_message,
    get_protected_message,
    get_admin_message,
    get_single_problem_statement_old,
    get_user_by_id_old,
    save_helping_status_old,
    save_npo,
    save_profile_metadata_old,
    save_problem_statement_old,
    update_npo,
    remove_npo,
    get_npo_list,
    get_single_npo,
    get_single_hackathon_event,
    get_single_hackathon_id,
    save_hackathon,
    get_teams_list,
    save_team,
    unjoin_team,
    join_team,
    get_hackathon_list,
    link_problem_statements_to_events_old,
    save_news,
    save_lead_async,
    get_news
)

   


bp_name = 'api-messages'
bp_url_prefix = '/api/messages'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


@bp.route("/public")
def public():
    return vars(get_public_message())


@bp.route("/protected")
@auth.require_user
def protected():
    return vars(get_protected_message())


@bp.route("/admin")
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def admin():    
    return vars(get_admin_message())


#
# Nonprofit Related Endpoints

@bp.route("/npo", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def add_npo(): 
    return vars(save_npo(request.get_json()))

@bp.route("/npo/edit", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def edit_npo(): 
    return vars(update_npo(request.get_json()))

@bp.route("/npo", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
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
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def add_hackathon():
    return vars(save_hackathon(request.get_json()))


@bp.route("/hackathons", methods=["GET"])
def list_hackathons():
    arg = request.args.get("current") 
    if arg != None and arg.lower() == "current":
        return get_hackathon_list("current")
    if arg != None and arg.lower() == "previous":
        return get_hackathon_list("previous")
    else:
        return get_hackathon_list() #all


@bp.route("/hackathon/<event_id>", methods=["GET"])
def get_single_hackathon_by_event(event_id):
    return (get_single_hackathon_event(event_id))

@bp.route("/hackathon/id/<id>", methods=["GET"])
def get_single_hackathon_by_id(id):
    return (get_single_hackathon_id(id))

@bp.route("/teams", methods=["GET"])
def get_teams():
    return (get_teams_list())

# Get a single team by id
@bp.route("/team/<team_id>", methods=["GET"])
def get_team(team_id):
    return (get_teams_list(team_id))


@auth.require_user
@bp.route("/team", methods=["POST"])
def add_team():
    if auth_user and auth_user.user_id:
        return save_team(auth_user.user_id, request.get_json())
    else:
        print("** ERROR Could not obtain user details for POST /team")
        return None


@bp.route("/team", methods=["DELETE"])
@auth.require_user
def remove_user_from_team():
    
    if auth_user and auth_user.user_id:        
        return vars(unjoin_team(auth_user.user_id, request.get_json()))
    else:
        print("** ERROR Could not obtain user details for DELETE /team")
        return None


@bp.route("/team", methods=["PATCH"])
@auth.require_user
def add_user_to_team():
    if auth_user and auth_user.user_id:
        return vars(join_team(auth_user.user_id, request.get_json()))
    else:
        print("** ERROR Could not obtain user details for PATCH /team")
        return None


# Used to provide feedback details - user must be logged in
@bp.route("/feedback/<user_id>")
@auth.require_user
def feedback(user_id):
    # TODO: This is stubbed out, need to change with new function for get_feedback
    return vars(get_profile_metadata_old(user_id))

# Used to provide feedback details - public with key needed
@bp.route("/news", methods=["POST"])
def store_news():    
    # Check header for token
    # if token is valid, store news
    # else return 401
    token = request.headers.get("X-Api-Key")
    # Check BACKEND_NEWS_TOKEN
    if token == None or token != os.getenv("BACKEND_NEWS_TOKEN"):
        return "Unauthorized", 401
    else:
        return vars(save_news(request.get_json()))
    
@bp.route("/news", methods=["GET"])
def read_news():
    limit_arg = request.args.get("limit")  # Get the value of the 'limit' parameter from the query string
    # Log
    print(f"limit_arg: {limit_arg}")

    # If limit is set, convert to int
    limit=3
    if limit_arg:
        limit = int(limit_arg)
    
    return vars(get_news(news_limit=limit, news_id=None))  # Pass the 'limit' parameter to the get_news() function

# Get news by id
@bp.route("/news/<id>", methods=["GET"])
def get_single_news(id):
    return vars(get_news(news_limit=1,news_id=id))


@bp.route("/lead", methods=["POST"])
async def store_lead():    
    if await save_lead_async(request.get_json()) == False:
        return "Unauthorized", 401
    else:
        return "OK", 200
    

# -------------------- Problem Statement routes to be deleted --------------------------- #
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
@bp.route("/problem_statements/events", methods=["PATCH"])
def update_problem_statement_events_link():    
    return vars(link_problem_statements_to_events_old(request.get_json()))

# Used to register when person says they are helping, not helping
# TODO: This route feels like it should be relative to a problem statement. NOT a user.
@bp.route("/profile/helping", methods=["POST"])
@auth.require_user
def register_helping_status():
    if auth_user and auth_user.user_id:
        # todo
        return vars(save_helping_status_old(auth_user.user_id, request.get_json()))
    else:
        print("** ERROR Could not obtain user details for POST /profile/helping")
        return None
    
@bp.route("/problem_statement", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def add_problem_statement():
    return vars(save_problem_statement_old(request.get_json()))

@bp.route("/problem_statements", methods=["GET"])
def get_problem_statments():    
    return get_problem_statement_list_old()

@bp.route("/problem_statement/<project_id>", methods=["GET"])
def get_single_problem(project_id):
    return (get_single_problem_statement_old(project_id))

# --------------------- TO BE REPLACED ROUTES ------------------------------------------#

# Used to provide profile details - user must be logged in
@bp.route("/profile", methods=["GET"])
@auth.require_user
def profile():            
    # user_id is a uuid from Propel Auth
    if auth_user and auth_user.user_id:        
        return vars(get_profile_metadata_old(auth_user.user_id))
    else:
        return None

@bp.route("/profile", methods=["POST"])
@auth.require_user
def save_profile():        
    if auth_user and auth_user.user_id:        
        return vars(save_profile_metadata_old(auth_user.user_id, request.get_json()))
    else:
        return None

# Get user profile by user id
@bp.route("/profile/<id>", methods=["GET"])
def get_profile_by_id(id):
    return get_user_by_id_old(id)

