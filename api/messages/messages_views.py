import os 
from common.log import get_logger, debug, error
import json
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
    get_volunteer_checked_in_by_event,
    save_helping_status_old,
    save_npo,
    save_profile_metadata_old,
    save_problem_statement_old,
    update_npo,
    remove_npo,
    get_npo_list,
    get_single_npo,
    get_npo_by_hackathon_id,
    get_npos_by_hackathon_id,
    get_single_hackathon_event,    
    single_add_volunteer,
    get_single_hackathon_id,
    add_nonprofit_to_hackathon,
    remove_nonprofit_from_hackathon,
    save_hackathon,
    create_hackathon,
    get_hackathon_request_by_id,
    update_hackathon_request,
    update_hackathon_volunteers,
    get_teams_list,
    get_team,
    get_teams_batch,
    get_teams_by_event_id,
    save_team,
    unjoin_team,
    join_team,
    get_hackathon_list,
    save_news,
    save_lead_async,
    get_news,
    get_all_profiles,
    save_npo_application,
    get_npo_applications,
    update_npo_application,
    get_github_profile,
    get_all_praises,
    get_praises_about_user,
    save_praise,
    save_feedback,
    get_user_feedback,
    get_volunteer_by_event,
    get_github_repos,
    get_user_giveaway,
    save_giveaway,
    get_all_giveaways,
    upload_image_to_cdn,
    save_onboarding_feedback,
)

logger = get_logger("messages_views")


bp_name = 'api-messages'
bp_url_prefix = '/api/messages'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

def get_authenticated_user_id():
    """Helper function to get authenticated user ID with proper error handling"""
    if not auth_user or not auth_user.user_id:
        error(logger, "Could not obtain user details - user not authenticated")
        return None
    return auth_user.user_id


@bp.route("/public")
def public():
    logger.info("GET /public called")
    return vars(get_public_message())


@bp.route("/protected")
@auth.require_user
def protected():
    logger.info("GET /protected called")
    return vars(get_protected_message())


@bp.route("/admin")
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def admin():
    logger.info("GET /admin called")
    return vars(get_admin_message())


#
# Nonprofit Related Endpoints

@bp.route("/npo", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_npo():
    logger.info("POST /npo called")
    return vars(save_npo(request.get_json()))

@bp.route("/npo", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def edit_npo():
    logger.info("PATCH /npo called")
    return vars(update_npo(request.get_json()))

@bp.route("/npo", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin")
def delete_npo():
    logger.info("DELETE /npo called")
    return vars(remove_npo(request.get_json()))


@bp.route("/npos", methods=["GET"])
def get_npos():
    logger.info("GET /npos called")
    return (get_npo_list())

# Get nonprofits by hackathon
@bp.route("/npos/hackathon/<id>", methods=["GET"])
def get_npos_by_hackathon_id_api(id):
    logger.info(f"GET /npos/hackathon/{id} called")
    return (get_npos_by_hackathon_id(id=id))

@bp.route("/npo/<npo_id>", methods=["GET"])
def get_npo(npo_id):
    logger.info(f"GET /npo/{npo_id} called")
    return (get_single_npo(npo_id))

# This isn't authenticated, but we're using Google reCAPTCHA to prevent abuse
@bp.route("/npo/submit-application", methods=["POST"])
def submit_npo_application():
    logger.info("POST /npo/submit-application called")
    return vars(save_npo_application(request.get_json()))

@bp.route("/npo/applications", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_npo_applications_api():
    logger.info("GET /npo/applications called")
    return get_npo_applications()

@bp.route("/npo/applications/<application_id>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_npo_application_api(application_id):
    logger.info(f"PATCH /npo/applications/{application_id} called")
    user_id = get_authenticated_user_id()
    if user_id:
        return vars(update_npo_application(application_id, request.get_json(), user_id))
    return {"error": "Unauthorized"}, 401

#
# Hackathon Related Endpoints

@bp.route("/hackathon", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_hackathon():
    logger.info("POST /hackathon called")
    user_id = get_authenticated_user_id()
    if user_id:
        return vars(save_hackathon(request.get_json(), user_id))
    return {"error": "Unauthorized"}, 401

@bp.route("/hackathon", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_hackathon():
    logger.info("PATCH /hackathon called")
    user_id = get_authenticated_user_id()
    if user_id:
        return vars(save_hackathon(request.get_json(), user_id))
    return {"error": "Unauthorized"}, 401


@bp.route("/hackathons", methods=["GET"])
def list_hackathons():
    logger.info("GET /hackathons called")
    arg = request.args.get("current") 
    if arg and arg.lower() == "current":
        return get_hackathon_list("current")
    if arg and arg.lower() == "previous":
        return get_hackathon_list("previous")
    else:
        return get_hackathon_list("all") #all

@bp.route("/hackathon/nonprofit", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_nonprofit_to_hackathon_api():
    logger.info("POST /hackathon/nonprofit called")
    if auth_user and auth_user.user_id:
        return add_nonprofit_to_hackathon(request.get_json())

@bp.route("/hackathon/nonprofit", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def remove_nonprofit_from_hackathon_api():
    logger.info("DELETE /hackathon/nonprofit called")
    if auth_user and auth_user.user_id:
        return remove_nonprofit_from_hackathon(request.get_json())


@bp.route("/hackathon/<event_id>/mentor", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_single_mentor(event_id):
    logger.info(f"POST /hackathon/{event_id}/mentor called")
    if auth_user and auth_user.user_id:
        return vars(single_add_volunteer(event_id, request.get_json(), "mentor", auth_user.user_id))
    
@bp.route("/hackathon/<event_id>/judge", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_single_judge(event_id):
    logger.info(f"POST /hackathon/{event_id}/judge called")
    if auth_user and auth_user.user_id:
        return vars(single_add_volunteer(event_id, request.get_json(), "judge", auth_user.user_id))
    
@bp.route("/hackathon/<event_id>/volunteer", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_single_volunteer(event_id):
    logger.info(f"POST /hackathon/{event_id}/volunteer called")
    if auth_user and auth_user.user_id:
        return vars(single_add_volunteer(event_id, request.get_json(), "volunteer", auth_user.user_id))

@bp.route("/hackathon/<event_id>/sponsor", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_single_sponsor(event_id):
    logger.info(f"POST /hackathon/{event_id}/sponsor called")
    if auth_user and auth_user.user_id:
        return vars(single_add_volunteer(event_id, request.get_json(), "sponsor", auth_user.user_id))

@bp.route("/hackathon/<event_id>/hacker", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def add_single_hacker(event_id):
    logger.info(f"POST /hackathon/{event_id}/hacker called")
    if auth_user and auth_user.user_id:
        return vars(single_add_volunteer(event_id, request.get_json(), "hacker", auth_user.user_id))

@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
@bp.route("/hackathon/<event_id>/<volunteer_type>/checkins", methods=["GET"])
def get_volunteers_checked_in_by_event_api(event_id, volunteer_type):
    logger.info(f"GET /hackathon/{event_id}/{volunteer_type}/checked_in called")
    return (get_volunteer_checked_in_by_event(event_id, volunteer_type))



@bp.route("/hackathon/<event_id>", methods=["GET"])
def get_single_hackathon_by_event(event_id):
    logger.info(f"GET /hackathon/{event_id} called")
    return (get_single_hackathon_event(event_id))

# -- Public APIs that call get_volunteer_by_event with admin=False
@bp.route("/hackathon/<event_id>/mentor", methods=["GET"])
def get_volunteer_mentor_by_event_api(event_id):
    logger.info(f"GET /hackathon/{event_id}/mentor called")
    return (get_volunteer_by_event(event_id, "mentor"))

@bp.route("/hackathon/<event_id>/judge", methods=["GET"])
def get_volunteer_judge_by_event_api(event_id):
    logger.info(f"GET /hackathon/{event_id}/judge called")
    return (get_volunteer_by_event(event_id, "judge"))

@bp.route("/hackathon/<event_id>/volunteer", methods=["GET"])
def get_volunteer_volunteers_by_event_api(event_id):
    logger.info(f"GET /hackathon/{event_id}/volunteer called")
    return (get_volunteer_by_event(event_id, "volunteer"))

@bp.route("/hackathon/<event_id>/hacker", methods=["GET"])
def get_volunteer_hacker_by_event_api(event_id):
    logger.info(f"GET /hackathon/{event_id}/hacker called")
    return (get_volunteer_by_event(event_id, "hacker"))

@bp.route("/hackathon/<event_id>/sponsor", methods=["GET"])
def get_volunteer_sponsor_by_event_api(event_id):
    logger.info(f"GET /hackathon/{event_id}/sponsor called")
    return (get_volunteer_by_event(event_id, "sponsor"))

# -- Admin APIs that call get_volunteer_by_event with admin=True
@bp.route("/admin/hackathon/<event_id>/mentor", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_volunteer_mentor_by_event_admin_api(event_id):
    logger.info(f"GET /admin/hackathon/{event_id}/mentor called")
    return (get_volunteer_by_event(event_id, "mentor", admin=True))

@bp.route("/admin/hackathon/<event_id>/judge", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_volunteer_judge_by_event_admin_api(event_id):
    logger.info(f"GET /admin/hackathon/{event_id}/judge called")
    return (get_volunteer_by_event(event_id, "judge", admin=True))

@bp.route("/admin/hackathon/<event_id>/volunteer", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_volunteer_volunteers_by_event_admin_api(event_id):
    logger.info(f"GET /admin/hackathon/{event_id}/volunteer called")
    return (get_volunteer_by_event(event_id, "volunteer", admin=True))

@bp.route("/admin/hackathon/<event_id>/hacker", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_volunteer_hacker_by_event_admin_api(event_id):
    logger.info(f"GET /admin/hackathon/{event_id}/hacker called")
    return (get_volunteer_by_event(event_id, "hacker", admin=True))

@bp.route("/admin/hackathon/<event_id>/sponsor", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_volunteer_sponsor_by_event_admin_api(event_id):
    logger.info(f"GET /admin/hackathon/{event_id}/sponsor called")
    return (get_volunteer_by_event(event_id, "sponsor", admin=True))


# ------------------- PATCH ------------------- #
@bp.route("/hackathon/<event_id>/mentor", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_mentor_by_event_id(event_id):
    logger.info(f"PATCH /hackathon/{event_id}/mentor called")
    if auth_user and auth_user.user_id:
        return vars(update_hackathon_volunteers(event_id, "mentors", request.get_json(), auth_user.user_id))

@bp.route("/hackathon/<event_id>/judge", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_judge_by_event_id(event_id):
    logger.info(f"PATCH /hackathon/{event_id}/judge called")
    return vars(update_hackathon_volunteers(event_id, "judges", request.get_json(), auth_user.user_id))

@bp.route("/hackathon/<event_id>/volunteer", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_volunteer_by_event_id(event_id):
    logger.info(f"PATCH /hackathon/{event_id}/volunteer called")
    return vars(update_hackathon_volunteers(event_id, "volunteers", request.get_json(), auth_user.user_id))


@bp.route("/hackathon/<event_id>/sponsor", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_sponsor_by_event_id(event_id):
    logger.info(f"PATCH /hackathon/{event_id}/sponsor called")
    return vars(update_hackathon_volunteers(event_id, "sponsors", request.get_json(), auth_user.user_id))

@bp.route("/hackathon/<event_id>/hacker", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def update_hacker_by_event_id(event_id):
    logger.info(f"PATCH /hackathon/{event_id}/hacker called")
    return vars(update_hackathon_volunteers(event_id, "hackers", request.get_json(), auth_user.user_id))

@bp.route("/hackathon/id/<id>", methods=["GET"])
def get_single_hackathon_by_id(id):
    logger.info(f"GET /hackathon/id/{id} called")
    return (get_single_hackathon_id(id))

@bp.route("/teams", methods=["GET"])
def get_teams():
    logger.info("GET /teams called")
    return (get_teams_list())

# Get a single team by id
@bp.route("/team/<team_id>", methods=["GET"])
def get_team_api(team_id):
    logger.info(f"GET /team/{team_id} called")
    return (get_team(team_id))

@bp.route("/teams/batch", methods=["POST"])
def get_batch_teams():
    logger.info("POST /teams/batch called")
    return get_teams_batch(request.get_json())


@bp.route("/team/<event_id>", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def get_teams_by_event(event_id):
    """Get teams for a specific hackathon (used in admin judging assignment)."""
    logger.info(f"GET /team/{event_id} called - Getting teams by event")
    return get_teams_by_event_id(event_id)


@bp.route("/team", methods=["POST"])
@auth.require_user
def add_team():
    logger.info("POST /team called")
    # This route is kept for backward compatibility
    # New teams should use the /api/team/queue endpoint
    if auth_user and auth_user.user_id:
        return save_team(auth_user.user_id, request.get_json())
    else:
        error(logger, "Could not obtain user details for POST /team")
        return None


@bp.route("/team", methods=["DELETE"])
@auth.require_user
def remove_user_from_team():
    logger.info("DELETE /team called")
    if auth_user and auth_user.user_id:        
        return vars(unjoin_team(auth_user.user_id, request.get_json()))
    else:
        error(logger, "Could not obtain user details for DELETE /team")
        return None


@bp.route("/team", methods=["PATCH"])
@auth.require_user
def add_user_to_team():
    logger.info("PATCH /team called")
    if auth_user and auth_user.user_id:
        return vars(join_team(auth_user.user_id, request.get_json()))
    else:
        error(logger, "Could not obtain user details for PATCH /team")
        return None


# Used to provide feedback details - user must be logged in
@bp.route("/feedback/<user_id>")
@auth.require_user
def feedback(user_id):
    logger.info(f"GET /feedback/{user_id} called")
    # TODO: This is stubbed out, need to change with new function for get_feedback
    return vars(get_profile_metadata_old(user_id))

# Used to provide feedback details - public with key needed
@bp.route("/news", methods=["POST"])
def store_news():
    logger.info("POST /news called")
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
    logger.info("GET /news called")
    limit_arg = request.args.get("limit")  # Get the value of the 'limit' parameter from the query string
    # Log
    logger.info(f"Processing problem statements list with limit: {limit_arg}")

    # If limit is set, convert to int
    limit=3
    if limit_arg:
        limit = int(limit_arg)
    
    return vars(get_news(news_limit=limit, news_id=None))  # Pass the 'limit' parameter to the get_news() function

# Get news by id
@bp.route("/news/<id>", methods=["GET"])
def get_single_news(id):
    logger.info(f"GET /news/{id} called")
    return vars(get_news(news_limit=1,news_id=id))


@bp.route("/lead", methods=["POST"])
async def store_lead():
    logger.info("POST /lead called")
    if await save_lead_async(request.get_json()) == False:
        return "Unauthorized", 401
    else:
        return "OK", 200
    
# -------------------- Praises routes begin here --------------------------- #
@bp.route("/praise", methods=["POST"])
def store_praise():
    logger.info("POST /praise called")
    # Check header for token
    # if token is valid, store news
    # else return 401
    
    token = request.headers.get("X-Api-Key")
    json_data = request.get_json()
    sender_id = json_data.get("praise_sender")
    receiver_id = json_data.get("praise_receiver")

    # Check BACKEND_NEWS_TOKEN
    if token == None or token != os.getenv("BACKEND_PRAISE_TOKEN"):
        return "Unauthorized", 401
    elif sender_id == receiver_id:
        return "You cannot write a praise about yourself", 400
    else:
        logger.info(f"Request object: {json_data}")
        return vars(save_praise(json_data))

@bp.route("/praises", methods=["GET"])
def get_praises():
    logger.info("GET /praises called")
    # return all praise data about user with user_id in route
    return vars(get_all_praises())

@bp.route("/praise/<user_id>", methods=["GET"])
def get_praises_about_self(user_id):
    logger.info(f"GET /praise/{user_id} called")
    # return all praise data about user with user_id in route
    return vars(get_praises_about_user(user_id)) 
# -------------------- Praises routes end here --------------------------- #

# ------------- Onboarding Feedback routes begin here --------------------- #
@bp.route("/onboarding_feedback", methods=["POST"])
def submit_onboarding_feedback():
    logger.info("POST /onboarding_feedback called")
    logger.info(f"onboarding feedback saving now.. data: {request.get_json()}")
    return vars(save_onboarding_feedback(request.get_json()))

# -------------------- Problem Statement routes to be deleted --------------------------- #

# Used to register when person says they are helping, not helping
# TODO: This route feels like it should be relative to a problem statement. NOT a user.
@bp.route("/profile/helping", methods=["POST"])
@auth.require_user
def register_helping_status():
    logger.info("POST /profile/helping called")
    if auth_user and auth_user.user_id:
        # todo
        return vars(save_helping_status_old(auth_user.user_id, request.get_json()))
    else:
        error(logger, "Could not obtain user details for POST /profile/helping")
        return None
    
@bp.route("/problem_statement", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def add_problem_statement():
    logger.info("POST /problem_statement called")
    return vars(save_problem_statement_old(request.get_json()))

@bp.route("/problem_statements", methods=["GET"])
def get_problem_statments():
    logger.info("GET /problem_statements called")
    return get_problem_statement_list_old()

@bp.route("/problem_statement/<project_id>", methods=["GET"])
def get_single_problem(project_id):
    logger.info(f"GET /problem_statement/{project_id} called")
    return (get_single_problem_statement_old(project_id))

#
# --------------------- TO BE REPLACED ROUTES ------------------------------------------#

# Used to provide profile details - user must be logged in
@bp.route("/profile", methods=["GET"])
@auth.require_user
def profile():
    logger.info("GET /profile called")
    # user_id is a uuid from Propel Auth
    if auth_user and auth_user.user_id:        
        return get_profile_metadata_old(auth_user.user_id)
    else:
        return None

@bp.route("/profile", methods=["POST"])
@auth.require_user
def save_profile():
    logger.info("POST /profile called")
    if auth_user and auth_user.user_id:        
        return vars(save_profile_metadata_old(auth_user.user_id, request.get_json()))
    else:
        return None

@bp.route("/profile/github/<username>", methods=["GET"])
def get_github_profile_api(username):
    logger.info(f"GET /profile/github/{username} called")
    return get_github_profile(username)
    

@bp.route("/github-repos/<event_id>", methods=["GET"])
def get_github_repos(event_id):
    logger.info(f"GET /github-repos/{event_id} called")
    return get_github_repos(event_id)

# Get user profile by user id
@bp.route("/profile/<id>", methods=["GET"])
def get_profile_by_id(id):
    logger.info(f"GET /profile/{id} called")
    return get_user_by_id_old(id)


# Used to provide profile details - user must be logged in
@bp.route("/admin/profiles", methods=["GET"])
@auth.require_org_member_with_permission("profile.admin", req_to_org_id=getOrgId)
def all_profiles():
    logger.info("GET /admin/profiles called")
    return get_all_profiles()


@bp.route("/feedback", methods=["POST"])
@auth.require_user
def submit_feedback():
    logger.info("POST /feedback called")
    if auth_user and auth_user.user_id:
        return vars(save_feedback(auth_user.user_id, request.get_json()))
    else:
        return {"error": "Unauthorized"}, 401
    
@bp.route("/feedback", methods=["GET"])
@auth.require_user
def get_feedback():
    """
    Get feedback for a user - note that you can only get this for the current logged in user    
    """
    logger.info("GET /feedback called")
    if auth_user and auth_user.user_id:
        return get_user_feedback(auth_user.user_id)
    else:
        return {"error": "Unauthorized"}, 401
    

@bp.route("/giveaway", methods=["POST"])
@auth.require_user
def submit_giveaway():
    logger.info("POST /giveaway called")
    if auth_user and auth_user.user_id:
        return vars(save_giveaway(auth_user.user_id, request.get_json()))
    else:
        return {"error": "Unauthorized"}, 401


@bp.route("/giveaway", methods=["GET"])
@auth.require_user
def get_giveaway():
    """
    Get feedback for a user - note that you can only get this for the current logged in user    
    """
    logger.info("GET /giveaway called")
    if auth_user and auth_user.user_id:
        return get_user_giveaway(auth_user.user_id)
    else:
        return {"error": "Unauthorized"}, 401
    
@bp.route("/giveaway/admin", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_all_giveaways():
    logger.info("GET /giveaway/admin called")
    return get_all_giveaways()


@bp.route("/create-hackathon", methods=["POST"])
def submit_create_hackathon():
    logger.info("POST /create-hackathon called")
    return create_hackathon(request.get_json())

@bp.route("/create-hackathon/<request_id>", methods=["GET"])
def get_submitted_hackathon(request_id):
    logger.info(f"GET /create-hackathon/{request_id} called")
    return get_hackathon_request_by_id(request_id)

@bp.route("/create-hackathon/<request_id>", methods=["PATCH"])
def update_submitted_hackathon(request_id):
    logger.info(f"PATCH /create-hackathon/{request_id} called")
    return update_hackathon_request(request_id, request.get_json())


@bp.route("/upload-image", methods=["POST"])
@auth.require_user
#@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def upload_image():
    """
    Upload an image to CDN. Accepts binary data, base64, or standard image formats.
    """
    logger.info("POST /upload-image called")
    from api.messages.messages_service import upload_image_to_cdn
    
    if auth_user and auth_user.user_id:
        return upload_image_to_cdn(request)
    else:
        error(logger, "Could not obtain user details for POST /upload-image")
        return {"error": "Unauthorized"}, 401
