"""
`/api/checkin` — registration-desk routes.

Gate: `volunteer.admin` OR `checkin.admin` (see `common.auth.require_any_permission`).
`checkin.admin` is the only permission a desk helper needs; it unlocks nothing
else in the admin — in particular it can NOT reset check-ins
(`POST /<event>/reset/<type>` is `volunteer.admin` only). The legacy check-in routes in messages_views (frozen) stay
`volunteer.admin`-only; the frontend workbench no longer calls them.
"""
from flask import Blueprint, abort, request

from common.auth import CHECKIN_PERMISSIONS, auth, auth_user, getOrgId, require_any_permission
from common.log import get_logger
from api.checkin import checkin_service

logger = get_logger("checkin_views")

bp_name = "api-checkin"
bp_url_prefix = "/api/checkin"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


def _check_type(volunteer_type):
    if not checkin_service.is_valid_type(volunteer_type):
        abort(404)
    return volunteer_type.lower()


@bp.route("/<event_id>/roster/<volunteer_type>", methods=["GET"])
@require_any_permission(CHECKIN_PERMISSIONS, req_to_org_id=getOrgId)
def checkin_roster(event_id, volunteer_type):
    volunteer_type = _check_type(volunteer_type)
    rows = checkin_service.get_checkin_roster(event_id, volunteer_type)
    return {"data": rows, "count": len(rows)}


@bp.route("/<event_id>/checkins/<volunteer_type>", methods=["GET"])
@require_any_permission(CHECKIN_PERMISSIONS, req_to_org_id=getOrgId)
def checkin_list(event_id, volunteer_type):
    volunteer_type = _check_type(volunteer_type)
    rows = checkin_service.get_checked_in(event_id, volunteer_type)
    return {"data": rows, "count": len(rows)}


@bp.route("/<event_id>/<volunteer_type>/<volunteer_id>", methods=["POST"])
@require_any_permission(CHECKIN_PERMISSIONS, req_to_org_id=getOrgId)
def check_in(event_id, volunteer_type, volunteer_id):
    volunteer_type = _check_type(volunteer_type)
    try:
        result = checkin_service.check_in_volunteer(
            event_id, volunteer_type, volunteer_id, auth_user.user_id
        )
    except checkin_service.CheckInError as e:
        return {"error": e.error, "message": e.message}, e.status
    return result


@bp.route("/<event_id>/reset/<volunteer_type>", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def reset_checkins(event_id, volunteer_type):
    """Clear check-ins for one event + role. Full admins only — desk helpers
    (`checkin.admin`) can check people in but never undo everyone's check-in.

    Body: {"volunteer_ids": [...]} or {"all": true}. An empty body is a 400 —
    it must never reset everyone by accident.
    """
    volunteer_type = _check_type(volunteer_type)
    body = request.get_json(silent=True) or {}
    ids = body.get("volunteer_ids")
    reset_all = body.get("all") is True

    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            return {"error": "invalid_ids", "message": "volunteer_ids must be a list of ids."}, 400
        if len(ids) > checkin_service.MAX_RESET_IDS:
            return {
                "error": "too_many_ids",
                "message": f"Reset at most {checkin_service.MAX_RESET_IDS} people at a time.",
            }, 400
        if not ids:
            return {"error": "missing_scope", "message": "Pick at least one person to reset."}, 400
    elif not reset_all:
        return {
            "error": "missing_scope",
            "message": 'Send {"volunteer_ids": [...]} or {"all": true}.',
        }, 400

    result = checkin_service.reset_checkins(
        event_id, volunteer_type, auth_user.user_id,
        volunteer_ids=None if ids is None else ids,
    )
    return result
