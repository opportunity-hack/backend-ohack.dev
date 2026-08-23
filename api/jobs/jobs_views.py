"""Volunteer job board routes.

Public: listing pages at /jobs on the frontend (Google-for-Jobs SEO).
Applicant routes require login (matches the mentor/judge application forms) —
the resume/video signed-upload flow depends on a resolved user doc.
Admin CRUD is volunteer.admin-gated, consumed by /admin/jobs.
"""

from flask import Blueprint, request

from common.log import get_logger
from common.auth import auth, auth_user, getOrgId
from api.jobs.jobs_service import (
    get_public_listings,
    get_public_listing,
    admin_list_listings,
    admin_create_listing,
    admin_update_listing,
    admin_delete_listing,
    create_resume_upload_url,
    submit_application,
    get_my_application,
    admin_list_applications,
    admin_update_application,
    admin_decide_application,
)

logger = get_logger(__name__)

bp = Blueprint("jobs", __name__, url_prefix="/api")


def _actor_from_request():
    try:
        return {
            "propel_user_id": auth_user.user_id if auth_user else None,
            "email": getattr(auth_user, "email", None) if auth_user else None,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@bp.route("/jobs", methods=["GET"])
def list_jobs():
    return {"listings": get_public_listings()}


@bp.route("/jobs/<slug>", methods=["GET"])
def get_job(slug):
    listing = get_public_listing(slug)
    if listing is None:
        return {"error": "Listing not found"}, 404
    return listing


# ---------------------------------------------------------------------------
# Applicant (login required)
# ---------------------------------------------------------------------------

@bp.route("/jobs/apply/resume-upload-url", methods=["POST"])
@auth.require_user
def resume_upload_url():
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    payload, status = create_resume_upload_url(
        auth_user.user_id, data.get("content_type"), data.get("content_length"))
    return payload, status


@bp.route("/jobs/<slug>/apply", methods=["POST"])
@auth.require_user
def apply_for_job(slug):
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    logger.info(f"POST /jobs/{slug}/apply called")
    payload, status = submit_application(
        auth_user.user_id, request.remote_addr, slug, request.get_json())
    return payload, status


@bp.route("/jobs/<slug>/applications/me", methods=["GET"])
@auth.require_user
def my_application(slug):
    if not (auth_user and auth_user.user_id):
        return {"error": "Unauthorized"}, 401
    payload, status = get_my_application(auth_user.user_id, slug)
    return payload, status


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@bp.route("/jobs/admin/listings", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_listings():
    payload, status = admin_list_listings()
    return payload, status


@bp.route("/jobs/admin/listings", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_post_listing():
    logger.info("POST /jobs/admin/listings called")
    payload, status = admin_create_listing(request.get_json(), _actor_from_request())
    return payload, status


@bp.route("/jobs/admin/listings/<slug>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_patch_listing(slug):
    logger.info(f"PATCH /jobs/admin/listings/{slug} called")
    payload, status = admin_update_listing(slug, request.get_json(), _actor_from_request())
    return payload, status


@bp.route("/jobs/admin/listings/<slug>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_delete_listing_route(slug):
    logger.info(f"DELETE /jobs/admin/listings/{slug} called")
    payload, status = admin_delete_listing(slug)
    return payload, status


@bp.route("/jobs/admin/applications", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_applications():
    payload, status = admin_list_applications(request.args.get("listing_slug"))
    return payload, status


@bp.route("/jobs/admin/applications/<application_id>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_patch_application(application_id):
    logger.info(f"PATCH /jobs/admin/applications/{application_id} called")
    payload, status = admin_update_application(application_id, request.get_json(), _actor_from_request())
    return payload, status


@bp.route("/jobs/admin/applications/<application_id>/decision", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_post_decision(application_id):
    logger.info(f"POST /jobs/admin/applications/{application_id}/decision called")
    payload, status = admin_decide_application(application_id, request.get_json(), _actor_from_request())
    return payload, status
