"""Meeting scheduling poll routes (see ``meet_service`` for the rules).

Public (no login; the vote is reCAPTCHA-gated):
  GET  /api/meet/<code>                 poll + everyone's answers (no emails)
  POST /api/meet/<code>/votes           add or change your answers

Admin (``volunteer.admin``):
  GET    /api/meet/admin/polls                      list
  POST   /api/meet/admin/polls                      create
  GET    /api/meet/admin/polls/<code>               detail incl. emails
  PATCH  /api/meet/admin/polls/<code>               edit / open / close
  DELETE /api/meet/admin/polls/<code>               delete poll + responses
  DELETE /api/meet/admin/polls/<code>/votes/<id>    remove one response
  POST   /api/meet/admin/polls/<code>/finalize      pick the time, send invites

View params == URL params only; admin identity is read from ``auth_user``.
"""
from flask import Blueprint, request

from common.auth import auth, auth_user, getOrgId
from common.log import get_logger
from api.meet import meet_service

logger = get_logger(__name__)

bp = Blueprint("meet", __name__, url_prefix="/api")

_INTERNAL_ERROR = (
    {"success": False, "error": "internal_error",
     "message": "An error occurred while processing your request"},
    500,
)


def _admin_from_request():
    """Admin identity from the verified PropelAuth token, never the body."""
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


def _run(label, fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        logger.exception("Meet %s failed: %s", label, str(e))
        return _INTERNAL_ERROR


@bp.route("/meet/<code>", methods=["GET"])
def get_poll(code):
    return _run("get", meet_service.get_public_poll, code)


@bp.route("/meet/<code>/votes", methods=["POST"])
def submit_vote(code):
    return _run("vote", meet_service.submit_vote, code, request.get_json(silent=True))


@bp.route("/meet/admin/polls", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_polls():
    return _run("admin list", meet_service.admin_list_polls)


@bp.route("/meet/admin/polls", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_create_poll():
    return _run("admin create", meet_service.admin_create_poll,
                request.get_json(silent=True), _admin_from_request())


@bp.route("/meet/admin/polls/<code>", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_poll(code):
    return _run("admin get", meet_service.admin_get_poll, code)


@bp.route("/meet/admin/polls/<code>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_update_poll(code):
    return _run("admin update", meet_service.admin_update_poll,
                code, request.get_json(silent=True))


@bp.route("/meet/admin/polls/<code>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_delete_poll(code):
    return _run("admin delete", meet_service.admin_delete_poll, code)


@bp.route("/meet/admin/polls/<code>/votes/<vote_id>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_delete_vote(code, vote_id):
    return _run("admin delete vote", meet_service.admin_delete_vote, code, vote_id)


@bp.route("/meet/admin/polls/<code>/finalize", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_finalize_poll(code):
    return _run("admin finalize", meet_service.admin_finalize_poll,
                code, request.get_json(silent=True), _admin_from_request())
