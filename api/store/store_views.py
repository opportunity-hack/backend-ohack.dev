from flask import Blueprint, jsonify, request
import os
from common.log import get_logger
from common.auth import auth
from api.store.store_service import (
    create_order,
    get_all_orders,
    get_order_by_id,
    get_order_by_session_id,
    update_order,
)

logger = get_logger(__name__)
bp = Blueprint('store', __name__, url_prefix='/api')


def getOrgId(req):
    return req.headers.get("X-Org-Id")


def verify_webhook_secret(req):
    """Verify the webhook shared secret from the request header."""
    expected = os.environ.get('STORE_WEBHOOK_SECRET', '')
    provided = req.headers.get('X-Webhook-Secret', '')
    return expected and provided and expected == provided


@bp.route("/store/orders", methods=["POST"])
def handle_create_order():
    """
    Webhook endpoint to create a new store order.
    Authenticated via X-Webhook-Secret header (machine-to-machine).
    """
    try:
        if not verify_webhook_secret(request):
            logger.warning("Invalid or missing webhook secret for store order creation")
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        if not data:
            logger.warning("Empty request body for store order")
            return jsonify({"success": False, "error": "Empty request body"}), 400

        result = create_order(data)

        if result.get('success'):
            return jsonify(result), 201
        return jsonify(result), 400

    except Exception as e:
        logger.exception("Error creating store order: %s", str(e))
        return jsonify({
            "success": False,
            "error": "An error occurred while processing your request"
        }), 500


@bp.route("/store/orders", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_list_orders():
    """Admin endpoint to list all store orders."""
    try:
        result = get_all_orders()
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 500
    except Exception as e:
        logger.exception("Error listing store orders: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/store/orders/<order_id>", methods=["GET"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_get_order(order_id):
    """Admin endpoint to get a single order."""
    try:
        result = get_order_by_id(order_id)
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 404
    except Exception as e:
        logger.exception("Error fetching store order: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/store/orders/<order_id>", methods=["PATCH"])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_update_order(order_id):
    """Admin endpoint to update order status, tracking, or notes."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Empty request body"}), 400

        result = update_order(order_id, data)
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 500
    except Exception as e:
        logger.exception("Error updating store order: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/store/orders/by-session/<session_id>", methods=["GET"])
def get_order_by_stripe_session(session_id):
    """Public endpoint to get limited order info by Stripe session ID (for success page)."""
    try:
        result = get_order_by_session_id(session_id)
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 404
    except Exception as e:
        logger.exception("Error fetching order by session: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500
