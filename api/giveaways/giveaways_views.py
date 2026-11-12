"""`/api/giveaways` — giveaways v2 (Nov 2026). See README.md here.

Participant routes need a logged-in user; admin routes need `volunteer.admin`.
Every error is `{"error": <code>, "message": <copy>}` with a 4xx/5xx status.
The legacy `/api/messages/giveaway*` routes (frozen messages_views) stay for
old clients; the current frontend never calls them.
"""
from flask import Blueprint, request

from common.auth import auth, auth_user, getOrgId
from common.log import get_logger
from api.giveaways import giveaways_service as svc

logger = get_logger("api.giveaways.giveaways_views")

bp_name = "api-giveaways"
bp_url_prefix = "/api/giveaways"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


def _propel_id():
    return auth_user.user_id if auth_user else None


def _actor():
    if not auth_user:
        return None
    return getattr(auth_user, "email", None) or auth_user.user_id


def _body():
    return request.get_json(silent=True)


def _run(fn, *args):
    try:
        return fn(*args)
    except svc.GiveawayError as e:
        return {"error": e.error, "message": e.message}, e.status


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

@bp.route("/open", methods=["GET"])
@auth.require_user
def open_giveaways():
    return _run(svc.get_open_giveaways, _propel_id())


# POST, not PUT: the CORS config (api/__init__.py) allows GET/POST/PATCH/DELETE only.
@bp.route("/<giveaway_id>/entry", methods=["POST"])
@auth.require_user
def save_entry(giveaway_id):
    return _run(svc.save_entry, _propel_id(), giveaway_id, _body())


@bp.route("/<giveaway_id>/entry", methods=["DELETE"])
@auth.require_user
def withdraw_entry(giveaway_id):
    return _run(svc.withdraw_entry, _propel_id(), giveaway_id)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@bp.route("/admin", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list():
    return _run(svc.admin_list_campaigns)


@bp.route("/admin", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_create():
    return _run(svc.admin_create_campaign, _body(), _actor())


@bp.route("/admin/<giveaway_id>", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get(giveaway_id):
    return _run(svc.admin_get_campaign, giveaway_id)


@bp.route("/admin/<giveaway_id>", methods=["PATCH"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_update(giveaway_id):
    return _run(svc.admin_update_campaign, giveaway_id, _body(), _actor())


@bp.route("/admin/<giveaway_id>/entries", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_entries(giveaway_id):
    return _run(svc.admin_list_entries, giveaway_id)


@bp.route("/admin/<giveaway_id>/draw/start", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_draw_start(giveaway_id):
    return _run(svc.admin_start_draw, giveaway_id, _body(), _actor())


@bp.route("/admin/<giveaway_id>/draw/results", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_draw_result(giveaway_id):
    return _run(svc.admin_record_result, giveaway_id, _body(), _actor())


@bp.route("/admin/<giveaway_id>/draw/undo", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_draw_undo(giveaway_id):
    return _run(svc.admin_undo_result, giveaway_id, _body(), _actor())


@bp.route("/admin/<giveaway_id>/draw/reset", methods=["POST"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_draw_reset(giveaway_id):
    return _run(svc.admin_reset_draw, giveaway_id, _actor())
