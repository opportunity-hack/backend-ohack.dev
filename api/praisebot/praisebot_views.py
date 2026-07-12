from flask import Blueprint, jsonify, request

from api.praisebot.praisebot_service import (
    create_config_doc,
    delete_config_doc,
    get_full_config,
    update_config_doc,
)
from common.auth import auth, auth_user
from common.log import get_logger
from common.utils.api_key import check_api_key

logger = get_logger("praisebot_views")

bp = Blueprint('praisebot', __name__, url_prefix='/api')


def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")


def _actor_from_request():
    try:
        return {
            "propel_user_id": auth_user.user_id if auth_user else None,
            "email": getattr(auth_user, "email", None) if auth_user else None,
        }
    except Exception:
        return None


@bp.route("/praise-bot/config", methods=["GET"])
def bot_get_config():
    """Bot-facing config read, authed via X-Api-Key (no PropelAuth)."""
    if not check_api_key(request, "BACKEND_BOT_CONFIG_TOKEN", "BACKEND_PRAISE_TOKEN"):
        return "Unauthorized", 401
    return jsonify(get_full_config())


@bp.route("/praise-bot/admin/config", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_config():
    return jsonify(get_full_config(include_audit=True))


@bp.route("/praise-bot/admin/config", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_create_config():
    body, status = create_config_doc(request.get_json(), _actor_from_request())
    return jsonify(body), status


@bp.route("/praise-bot/admin/config/<doc_id>", methods=["PATCH"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_update_config(doc_id):
    body, status = update_config_doc(doc_id, request.get_json(), _actor_from_request())
    return jsonify(body), status


@bp.route("/praise-bot/admin/config/<doc_id>", methods=["DELETE"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_delete_config(doc_id):
    body, status = delete_config_doc(doc_id)
    return jsonify(body), status
