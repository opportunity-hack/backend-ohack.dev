from model.user import User
from services import users_service
from services import user_slug_service
from services import problem_statements_service
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
    """Canonical own-profile read: the flat build_profile_response dict."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    profile_data = users_service.get_profile_metadata(auth_user.user_id)
    if profile_data is None:
        # Identity couldn't be resolved by any tier (propel_id, OAuth, metadata)
        return {"error": "Unable to resolve user profile"}, 503
    return profile_data


@bp.route("/profile", methods=["POST"])
@auth.require_user
def save_profile():
    """Canonical own-profile write. Returns the updated flat profile dict."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json(silent=True)
    if not data or "metadata" not in data:
        return {"error": "metadata is required"}, 400
    result = users_service.save_profile_metadata(auth_user.user_id, data)
    if result is None:
        return {"error": "Unable to resolve user profile"}, 404
    return result


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
        if u is None:
            return {"error": "User not found or unable to save volunteering time"}, 404
        return vars(u)
    else:
        return {"error": "Unauthorized"}, 401


@bp.route("/volunteering", methods=["GET"])
@auth.require_user
def get_volunteering_time():
    # Get url params
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')

    if auth_user and auth_user.user_id:
        result = users_service.get_volunteering_time(auth_user.user_id, start_date, end_date)
        if result is None:
            return {"error": "User not found"}, 404
        allVolunteering, totalActiveHours, totalCommitmentHours = result
        return {
            "totalActiveHours": totalActiveHours,
            "totalCommitmentHours": totalCommitmentHours,
            "allVolunteering": allVolunteering
        }
    else:
        return {"error": "Unauthorized"}, 401
    
    
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


@bp.route("/profile/helping", methods=["POST"])
@auth.require_user
def register_helping_status():
    """Canonical helping toggle (replaces POST /api/messages/profile/helping)."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json(silent=True) or {}
    for required in ("status", "problem_statement_id", "type"):
        if required not in data:
            return {"error": f"{required} is required"}, 400
    result = problem_statements_service.save_helping_status(auth_user.user_id, data)
    if result is None:
        return {"error": "Unable to resolve user or problem statement"}, 404
    return result


# Vanity profile slug (portfolio URL) — dedicated routes so slugs can never be
# set through the generic profile metadata POST (uniqueness would be bypassed).
@bp.route("/profile/slug", methods=["POST"])
@auth.require_user
def claim_profile_slug():
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    payload, status = user_slug_service.claim_profile_slug(auth_user.user_id, data.get("slug"))
    return payload, status


@bp.route("/profile/slug/check/<slug>", methods=["GET"])
@auth.require_user
def check_profile_slug(slug):
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    return user_slug_service.check_slug_availability(slug)


@bp.route("/profile/visibility", methods=["PATCH"])
@auth.require_user
def set_profile_visibility():
    """Portfolio master toggle: private (default) or public (search-indexable)."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    payload, status = users_service.set_profile_visibility(auth_user.user_id, data.get("visibility"))
    return payload, status


@bp.route("/portfolio/sitemap", methods=["GET"])
def get_portfolio_sitemap():
    """Public feed of opted-in portfolio slugs for the frontend server-sitemap."""
    return {"portfolios": users_service.get_searchable_portfolio_sitemap()}


@bp.route("/profile/bio-video/upload-url", methods=["POST"])
@auth.require_user
def create_bio_video_upload_url():
    """Mint a signed GCS PUT URL — video bytes never pass through this API."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    payload, status = users_service.create_bio_video_upload_url(
        auth_user.user_id, data.get("content_type"), data.get("content_length"))
    return payload, status


@bp.route("/profile/bio-video", methods=["POST"])
@auth.require_user
def set_bio_video_url():
    """Set (own-CDN upload or YouTube/Vimeo/Loom link) or clear the bio video."""
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    payload, status = users_service.set_bio_video_url(auth_user.user_id, data.get("url"))
    return payload, status


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
    """Get privacy-filtered public profile by database ID or vanity slug (cached)"""
    profile_data = users_service.get_portfolio_profile(user_id)
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


@bp.route("/github/<github_username>", methods=["GET"])
def get_slack_id_by_github(github_username):
    """Map a GitHub username to a Slack user ID."""
    from services.users_service import get_slack_user_id_by_github
    slack_id = get_slack_user_id_by_github(github_username)
    if slack_id:
        return {"slack_user_id": slack_id}
    return {"error": "not found"}, 404