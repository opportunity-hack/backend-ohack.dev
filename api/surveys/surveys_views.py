from flask import Blueprint, jsonify, request
from common.log import get_logger
from common.auth import auth, auth_user, getOrgId
from api.surveys.surveys_service import (
    get_survey_context,
    submit_survey_response,
    get_event_survey_responses,
    get_event_survey_summary,
    get_cross_event_survey_overview,
)

logger = get_logger(__name__)
bp = Blueprint("surveys", __name__, url_prefix="/api")


def _propel_user_id():
    user = auth_user
    return getattr(user, "user_id", None) if user else None


@bp.route("/surveys/<event_id>/context", methods=["GET"])
@auth.optional_user
def survey_context(event_id):
    """Public. Tells the frontend the survey mode (live/post/upcoming), the
    caller's eligible roles, and whether a CAPTCHA token is needed."""
    try:
        result, status = get_survey_context(event_id, _propel_user_id())
        return jsonify(result), status
    except Exception as e:
        logger.exception("Error fetching survey context: %s", str(e))
        return jsonify({"success": False, "error": "Failed to load the feedback form."}), 500


@bp.route("/surveys/<event_id>/responses", methods=["POST"])
@auth.optional_user
def submit_survey(event_id):
    """Public. Logged-in selected volunteers are trusted; everyone else
    (nonprofit partners, anonymous) must pass reCAPTCHA."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Empty request body"}), 400
    try:
        result, status = submit_survey_response(
            event_id, _propel_user_id(), data, ip_address=request.remote_addr
        )
        return jsonify(result), status
    except Exception as e:
        logger.exception("Error submitting survey response: %s", str(e))
        return jsonify({"success": False, "error": "An error occurred while saving your feedback."}), 500


@bp.route("/surveys/<event_id>/responses", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def list_survey_responses(event_id):
    """Admin: all responses for an event (optional ?mode=live|post)."""
    try:
        return jsonify(get_event_survey_responses(event_id, request.args.get("mode"))), 200
    except Exception as e:
        logger.exception("Error listing survey responses: %s", str(e))
        return jsonify({"success": False, "error": str(e), "responses": []}), 500


@bp.route("/surveys/<event_id>/summary", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def survey_summary(event_id):
    """Admin: light aggregate of responses for an event."""
    try:
        return jsonify(get_event_survey_summary(event_id)), 200
    except Exception as e:
        logger.exception("Error building survey summary: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/surveys/overview", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def surveys_overview():
    """Admin: cross-event aggregate for the 'Compare events' view (one scan,
    grouped by event_id; aggregates only, no PII)."""
    try:
        return jsonify(get_cross_event_survey_overview()), 200
    except Exception as e:
        logger.exception("Error building cross-event survey overview: %s", str(e))
        return jsonify({"success": False, "error": str(e), "events": []}), 500
