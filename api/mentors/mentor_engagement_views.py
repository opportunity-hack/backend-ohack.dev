"""Admin read route for mentor engagement (check-ins + mentor panel use)."""
import logging
from flask import Blueprint

from common.auth import auth, getOrgId
from api.mentors.mentor_engagement_service import get_mentor_engagement

logger = logging.getLogger("myapp")

bp = Blueprint("api-mentor-engagement", __name__, url_prefix="/api/admin/mentor-engagement")


@bp.route("/<event_id>", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def mentor_engagement_api(event_id):
    try:
        return get_mentor_engagement(event_id), 200
    except Exception as e:
        logger.exception("mentor engagement failed for %s: %s", event_id, e)
        return {"error": "engagement_unavailable",
                "message": "Could not load mentor engagement."}, 500
