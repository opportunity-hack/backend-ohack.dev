from flask import Blueprint, jsonify, request

from common.log import get_logger
from common.auth import auth, getOrgId
from api.feedback.feedback_service import (
    list_peer_feedback,
    list_onboarding_feedback,
)

logger = get_logger(__name__)
bp = Blueprint("feedback_admin", __name__, url_prefix="/api")


def _limit(default: int = 500, cap: int = 2000) -> int:
    try:
        return min(int(request.args.get("limit", default)), cap)
    except (TypeError, ValueError):
        return default


@bp.route("/admin/feedback/peer", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_peer_feedback():
    """Admin: all peer-to-peer feedback (newest first), names resolved."""
    try:
        return jsonify(list_peer_feedback(_limit())), 200
    except Exception as e:
        logger.exception("Error listing peer feedback: %s", str(e))
        return jsonify({"success": False, "error": str(e), "feedback": []}), 500


@bp.route("/admin/feedback/onboarding", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_onboarding_feedback():
    """Admin: all onboarding feedback (newest first) + rating distribution."""
    try:
        return jsonify(list_onboarding_feedback(_limit())), 200
    except Exception as e:
        logger.exception("Error listing onboarding feedback: %s", str(e))
        return jsonify({"success": False, "error": str(e), "onboarding_feedback": []}), 500
