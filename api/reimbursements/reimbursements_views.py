"""Reimbursement routes.

Public (no login, reCAPTCHA-gated like the contact form):
  POST /api/reimbursements                    submit a claim
  POST /api/reimbursements/receipt-upload-url mint a signed GCS PUT URL
  GET  /api/reimbursements/status            public status lookup

Admin (``volunteer.admin`` only; v1 — see the open decision in the PR about
a dedicated ``reimbursement.admin`` permission like ``checkin.admin``):
  GET   /api/reimbursements/admin            list, filter, paginate
  GET   /api/reimbursements/admin/<id>       full detail
  PATCH /api/reimbursements/admin/<id>       review (status + note)

View params == URL params only (PropelAuth decorators pass nothing else);
reviewer identity is read from ``auth_user`` inside the body. Every admin
route passes ``req_to_org_id=getOrgId``.
"""
from flask import Blueprint, request

from common.auth import auth, auth_user, getOrgId
from common.log import get_logger
from api.reimbursements import reimbursements_service

logger = get_logger(__name__)

bp = Blueprint("reimbursements", __name__, url_prefix="/api")


def _reviewer_from_request():
    """Reviewer identity from the verified PropelAuth token — never from the
    request body. Returns {propel_id, email, name} (values may be None)."""
    try:
        user = auth_user
        if user is None:
            return {}
        return {
            "propel_id": getattr(user, "user_id", None),
            "email": getattr(user, "email", None),
            "name": getattr(user, "name", None),
        }
    except Exception:
        return {}


@bp.route("/reimbursements", methods=["POST"])
def submit_reimbursement():
    """Public claim submission. Body is camelCase per the API contract."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return {
            "success": False,
            "error": "missing_fields",
            "message": "Empty request body",
        }, 400
    try:
        payload, status = reimbursements_service.submit_reimbursement(
            request.remote_addr, data
        )
    except Exception as e:
        logger.exception("Error processing reimbursement submission: %s", str(e))
        return {
            "success": False,
            "error": "internal_error",
            "message": "An error occurred while processing your request",
        }, 500
    return payload, status


@bp.route("/reimbursements/receipt-upload-url", methods=["POST"])
def receipt_upload_url():
    """Public signed-upload mint for a receipt file (bytes go straight to
    GCS, never through Flask). Body: {contentType, contentLength,
    recaptchaToken}."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return {
            "success": False,
            "error": "missing_fields",
            "message": "Empty request body",
        }, 400
    try:
        payload, status = reimbursements_service.mint_receipt_upload_url(
            request.remote_addr, data
        )
    except Exception as e:
        logger.exception("Error minting receipt upload URL: %s", str(e))
        return {"success": False, "error": "upload_url_failed",
                "message": "Could not create an upload URL"}, 500
    return payload, status


@bp.route("/reimbursements/status", methods=["GET"])
def reimbursement_status():
    """Public status lookup. Query: ?email=&reference_id=. Returns only the
    public projection — never receipt URLs or reviewer notes."""
    try:
        payload, status = reimbursements_service.get_reimbursement_status(
            request.args.get("email"), request.args.get("reference_id")
        )
    except Exception as e:
        logger.exception("Error looking up reimbursement status: %s", str(e))
        return {
            "success": False,
            "error": "internal_error",
            "message": "An error occurred while processing your request",
        }, 500
    return payload, status


@bp.route("/reimbursements/admin", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_reimbursements():
    """Admin list. Query: status, event_id, limit (default 50, max 200),
    offset. Returns {data, count} with count = total matching."""
    try:
        payload, status = reimbursements_service.admin_list_reimbursements(
            status=request.args.get("status"),
            event_id=request.args.get("event_id"),
            limit=request.args.get("limit", 50),
            offset=request.args.get("offset", 0),
        )
    except Exception as e:
        logger.exception("Error listing reimbursements: %s", str(e))
        return {"success": False, "error": "internal_error",
                "message": "An error occurred while processing your request"}, 500
    return payload, status


@bp.route("/reimbursements/admin/<reimbursement_id>", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_reimbursement(reimbursement_id):
    """Admin full detail: receipt final URL, status history, reviewer info."""
    try:
        payload, status = reimbursements_service.admin_get_reimbursement(
            reimbursement_id
        )
    except Exception as e:
        logger.exception("Error fetching reimbursement %s: %s", reimbursement_id, str(e))
        return {"success": False, "error": "internal_error",
                "message": "An error occurred while processing your request"}, 500
    return payload, status


@bp.route("/reimbursements/admin/<reimbursement_id>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_review_reimbursement(reimbursement_id):
    """Admin review. Body: {status, reviewNote, paymentReference (required
    when status is paid)} only — any status /
    reviewedBy / paidAt / statusHistory in the payload is ignored by the
    service. Self-approval is rejected with 403 self_approval."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return {
            "success": False,
            "error": "missing_fields",
            "message": "Empty request body",
        }, 400
    try:
        payload, status = reimbursements_service.admin_review_reimbursement(
            reimbursement_id, data, _reviewer_from_request()
        )
    except Exception as e:
        logger.exception("Error reviewing reimbursement %s: %s", reimbursement_id, str(e))
        return {"success": False, "error": "internal_error",
                "message": "An error occurred while processing your request"}, 500
    return payload, status
