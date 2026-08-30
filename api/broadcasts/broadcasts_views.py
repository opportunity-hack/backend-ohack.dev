"""Admin Resend segment/broadcast/batch-send endpoints.

Powers the /admin/communication Email tab: preview recipient sources
(profiles/volunteers/leads/slack/custom), sync them into a Resend segment
(background thread + polled status), create/send broadcasts, and the
transactional batch-send used by the personalized bulk path.

All routes are volunteer.admin-gated. Logic lives in
services/broadcasts_service.py.
"""

from flask import Blueprint, request

from common.log import get_logger
from common.auth import auth, auth_user, getOrgId
from services.broadcasts_service import (
    batch_send_emails,
    create_broadcast,
    get_broadcast,
    get_prune_status,
    get_sync_status,
    list_broadcasts,
    list_contacts,
    list_segments,
    preview_sources,
    send_broadcast,
    start_contact_prune,
    start_segment_sync,
)

logger = get_logger(__name__)

bp = Blueprint("broadcasts", __name__, url_prefix="/api")


def _actor_from_request():
    try:
        return {
            "propel_user_id": auth_user.user_id if auth_user else None,
            "email": getattr(auth_user, "email", None) if auth_user else None,
        }
    except Exception:
        return None


@bp.route("/admin/broadcasts/segments", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_segments():
    logger.info("GET /admin/broadcasts/segments called")
    msg, status_code = list_segments()
    return vars(msg), status_code


@bp.route("/admin/broadcasts/preview", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_preview_sources():
    logger.info("POST /admin/broadcasts/preview called")
    msg, status_code = preview_sources(request.get_json())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/segments/sync", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_start_segment_sync():
    logger.info("POST /admin/broadcasts/segments/sync called")
    msg, status_code = start_segment_sync(request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/segments/<segment_id>/sync-status", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_sync_status(segment_id):
    msg, status_code = get_sync_status(segment_id)
    return vars(msg), status_code


@bp.route("/admin/broadcasts", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_broadcasts():
    logger.info("GET /admin/broadcasts called")
    msg, status_code = list_broadcasts()
    return vars(msg), status_code


@bp.route("/admin/broadcasts", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_create_broadcast():
    logger.info("POST /admin/broadcasts called")
    msg, status_code = create_broadcast(request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/<broadcast_id>", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_broadcast(broadcast_id):
    msg, status_code = get_broadcast(broadcast_id)
    return vars(msg), status_code


@bp.route("/admin/broadcasts/<broadcast_id>/send", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_send_broadcast(broadcast_id):
    logger.info(f"POST /admin/broadcasts/{broadcast_id}/send called")
    msg, status_code = send_broadcast(broadcast_id, request.get_json(silent=True), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/batch-send", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_batch_send():
    logger.info("POST /admin/broadcasts/batch-send called")
    msg, status_code = batch_send_emails(request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/contacts", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_contacts():
    logger.info("GET /admin/broadcasts/contacts called")
    force = request.args.get("force", "false").lower() == "true"
    msg, status_code = list_contacts(force=force)
    return vars(msg), status_code


@bp.route("/admin/broadcasts/contacts/prune", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_start_contact_prune():
    logger.info("POST /admin/broadcasts/contacts/prune called")
    msg, status_code = start_contact_prune(request.get_json(), _actor_from_request())
    return vars(msg), status_code


@bp.route("/admin/broadcasts/contacts/prune-status", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_prune_status():
    msg, status_code = get_prune_status()
    return vars(msg), status_code
