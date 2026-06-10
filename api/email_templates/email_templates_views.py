"""Admin email template CRUD with version history.

Powers the /admin/communication template editor on the frontend. Templates
live in the email_templates collection (see services/email_templates_service)
with an append-only versions subcollection for history/revert. Seeded from
the original hardcoded frontend templates on first list call.
"""

from flask import Blueprint, request

from common.log import get_logger
from common.auth import auth, auth_user, getOrgId
from services.email_templates_service import (
    admin_list_templates,
    admin_create_template,
    admin_update_template,
    admin_delete_template,
    admin_get_template_versions,
    admin_revert_template,
    admin_seed_templates,
)

logger = get_logger(__name__)

bp = Blueprint("email_templates", __name__, url_prefix="/api")


def _actor_from_request():
    try:
        return {
            "propel_user_id": auth_user.user_id if auth_user else None,
            "email": getattr(auth_user, "email", None) if auth_user else None,
        }
    except Exception:
        return None


@bp.route("/admin/templates", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_templates():
    logger.info("GET /admin/templates called")
    return vars(admin_list_templates())


@bp.route("/admin/templates", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_post_template():
    logger.info("POST /admin/templates called")
    msg, status_code = admin_create_template(request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/templates/seed", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_post_template_seed():
    logger.info("POST /admin/templates/seed called")
    msg, status_code = admin_seed_templates()
    return vars(msg), status_code


@bp.route("/admin/templates/<template_id>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_patch_template(template_id):
    logger.info(f"PATCH /admin/templates/{template_id} called")
    msg, status_code = admin_update_template(template_id, request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/templates/<template_id>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_delete_template_route(template_id):
    logger.info(f"DELETE /admin/templates/{template_id} called")
    msg, status_code = admin_delete_template(template_id)
    return vars(msg), status_code


@bp.route("/admin/templates/<template_id>/versions", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_template_versions_route(template_id):
    logger.info(f"GET /admin/templates/{template_id}/versions called")
    msg, status_code = admin_get_template_versions(template_id)
    return vars(msg), status_code


@bp.route("/admin/templates/<template_id>/revert", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_post_template_revert(template_id):
    logger.info(f"POST /admin/templates/{template_id}/revert called")
    msg, status_code = admin_revert_template(template_id, request.get_json(), _actor_from_request())
    return vars(msg), status_code
