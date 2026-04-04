from typing import Dict, Any
import uuid
from datetime import datetime
import os
import pytz
import resend
from db.db import get_db
from common.log import get_logger
from common.utils.slack import send_slack

logger = get_logger(__name__)


def _get_current_timestamp() -> str:
    """Get current ISO timestamp in Arizona timezone."""
    az_timezone = pytz.timezone('US/Arizona')
    return datetime.now(az_timezone).isoformat()


def create_order(order_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new store order. Idempotent by stripeSessionId.

    Args:
        order_data: Order data from Stripe webhook

    Returns:
        Dict with success status and order ID
    """
    try:
        db = get_db()
        stripe_session_id = order_data.get('stripeSessionId')

        # Idempotency check: skip if order with same session already exists
        if stripe_session_id:
            existing = db.collection('store_orders').where(
                'stripeSessionId', '==', stripe_session_id
            ).limit(1).stream()
            for doc in existing:
                logger.info("Order already exists for session %s, skipping", stripe_session_id)
                return {"success": True, "id": doc.id, "duplicate": True}

        order_id = str(uuid.uuid4())
        timestamp = _get_current_timestamp()

        order = {
            'id': order_id,
            'stripeSessionId': order_data.get('stripeSessionId', ''),
            'stripePaymentIntentId': order_data.get('stripePaymentIntentId', ''),
            'status': 'new',
            'customerEmail': order_data.get('customerEmail', ''),
            'customerName': order_data.get('customerName', ''),
            'shippingAddress': order_data.get('shippingAddress', {}),
            'items': order_data.get('items', []),
            'subtotal': order_data.get('subtotal', 0),
            'total': order_data.get('total', 0),
            'currency': order_data.get('currency', 'usd'),
            'trackingNumber': '',
            'trackingCarrier': '',
            'adminNotes': '',
            'statusHistory': [
                {
                    'status': 'new',
                    'timestamp': timestamp,
                    'note': 'Order created from Stripe checkout'
                }
            ],
            'timestamp': timestamp,
            'updatedAt': timestamp,
        }

        db.collection('store_orders').document(order_id).set(order)
        logger.info("Created store order %s for %s", order_id, order.get('customerEmail'))

        # Send confirmation email
        try:
            send_order_confirmation_email(order)
            logger.info("Sent confirmation email for order %s", order_id)
        except Exception as e:
            logger.error("Failed to send confirmation email for order %s: %s", order_id, str(e))

        # Send admin notification email
        try:
            send_admin_order_notification(order)
            logger.info("Sent admin notification for order %s", order_id)
        except Exception as e:
            logger.error("Failed to send admin notification for order %s: %s", order_id, str(e))

        # Send Slack notification
        try:
            send_order_slack_notification(order)
            logger.info("Sent Slack notification for order %s", order_id)
        except Exception as e:
            logger.error("Failed to send Slack notification for order %s: %s", order_id, str(e))

        return {"success": True, "id": order_id}

    except Exception as e:
        logger.exception("Error creating store order: %s", str(e))
        return {"success": False, "error": str(e)}


def get_all_orders() -> Dict[str, Any]:
    """Get all store orders ordered by timestamp DESC."""
    try:
        db = get_db()
        orders = []
        docs = db.collection('store_orders').order_by('timestamp', direction='DESCENDING').stream()
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            orders.append(data)
        return {"success": True, "orders": orders}
    except Exception as e:
        logger.error("Error fetching store orders: %s", str(e))
        return {"success": False, "error": str(e), "orders": []}


def get_order_by_id(order_id: str) -> Dict[str, Any]:
    """Get a single order by its ID."""
    try:
        db = get_db()
        doc = db.collection('store_orders').document(order_id).get()
        if doc.exists:
            data = doc.to_dict()
            data['id'] = doc.id
            return {"success": True, "order": data}
        return {"success": False, "error": "Order not found"}
    except Exception as e:
        logger.error("Error fetching order %s: %s", order_id, str(e))
        return {"success": False, "error": str(e)}


def get_order_by_session_id(session_id: str) -> Dict[str, Any]:
    """Get an order by Stripe session ID. Returns limited fields for public access."""
    try:
        db = get_db()
        docs = db.collection('store_orders').where(
            'stripeSessionId', '==', session_id
        ).limit(1).stream()

        for doc in docs:
            data = doc.to_dict()
            # Return limited fields for public endpoint
            return {
                "success": True,
                "order": {
                    "id": data.get('id', doc.id),
                    "status": data.get('status'),
                    "customerName": data.get('customerName'),
                    "customerEmail": data.get('customerEmail'),
                    "items": data.get('items', []),
                    "subtotal": data.get('subtotal'),
                    "total": data.get('total'),
                    "currency": data.get('currency'),
                    "timestamp": data.get('timestamp'),
                }
            }

        return {"success": False, "error": "Order not found"}
    except Exception as e:
        logger.error("Error fetching order by session %s: %s", session_id, str(e))
        return {"success": False, "error": str(e)}


def update_order(order_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update an order's status, tracking, or admin notes.
    Appends to statusHistory if status changed.
    Sends status email if status changed.
    """
    try:
        db = get_db()

        # Fetch current order to detect status change
        doc = db.collection('store_orders').document(order_id).get()
        if not doc.exists:
            return {"success": False, "error": "Order not found"}

        current_order = doc.to_dict()
        old_status = current_order.get('status', 'new')
        new_status = data.get('status', old_status)
        timestamp = _get_current_timestamp()

        update_data = {'updatedAt': timestamp}

        if 'status' in data:
            update_data['status'] = data['status']
        if 'trackingNumber' in data:
            update_data['trackingNumber'] = data['trackingNumber']
        if 'trackingCarrier' in data:
            update_data['trackingCarrier'] = data['trackingCarrier']
        if 'adminNotes' in data:
            update_data['adminNotes'] = data['adminNotes']

        # Append to status history if status changed
        if old_status != new_status:
            status_history = current_order.get('statusHistory', [])
            status_history.append({
                'status': new_status,
                'timestamp': timestamp,
                'note': data.get('statusNote', f'Status changed from {old_status} to {new_status}')
            })
            update_data['statusHistory'] = status_history

        db.collection('store_orders').document(order_id).update(update_data)
        logger.info("Updated store order %s", order_id)

        # Send status email if status changed
        if old_status != new_status:
            try:
                updated_order = {**current_order, **update_data}
                send_order_status_email(updated_order, old_status, new_status)
                logger.info("Sent status update email for order %s", order_id)
            except Exception as e:
                logger.error("Failed to send status email for order %s: %s", order_id, str(e))

        return {"success": True}
    except Exception as e:
        logger.error("Error updating store order %s: %s", order_id, str(e))
        return {"success": False, "error": str(e)}


def _format_items_html(items):
    """Format order items as an HTML table."""
    rows = ""
    for item in items:
        rows += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{item.get('name', '')}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{item.get('description', '')}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee; text-align: center;">{item.get('quantity', 1)}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee; text-align: right;">${item.get('totalPrice', 0):.2f}</td>
        </tr>"""

    return f"""
    <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <thead>
            <tr style="background-color: #f5f5f5;">
                <th style="padding: 8px; text-align: left;">Item</th>
                <th style="padding: 8px; text-align: left;">Details</th>
                <th style="padding: 8px; text-align: center;">Qty</th>
                <th style="padding: 8px; text-align: right;">Price</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
        <tfoot>
            <tr style="font-weight: bold;">
                <td colspan="3" style="padding: 8px; text-align: right;">Total:</td>
                <td style="padding: 8px; text-align: right;">${sum(i.get('totalPrice', 0) for i in items):.2f}</td>
            </tr>
        </tfoot>
    </table>"""


def send_order_confirmation_email(order_data: Dict[str, Any]) -> bool:
    """Send confirmation email to customer after purchase."""
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
        return False

    resend.api_key = resend_api_key
    email = order_data.get('customerEmail', '')
    name = order_data.get('customerName', '')
    order_id = order_data.get('id', '')
    items = order_data.get('items', [])

    try:
        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "reply_to": "questions@ohack.org",
            "cc": ["questions@ohack.org"],
            "subject": f"Order Confirmation #{order_id[:8]} - Opportunity Hack Store",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2c5aa0;">Thank you for your order!</h2>
            <p>Hello {name},</p>
            <p>We've received your order and it's being processed. All proceeds support nonprofits through technology at Opportunity Hack.</p>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <h3 style="margin-top: 0;">Order Details</h3>
                <p><strong>Order ID:</strong> {order_id[:8]}</p>
                <p><strong>Date:</strong> {order_data.get('timestamp', '')}</p>
            </div>

            {_format_items_html(items)}

            <p><strong>Total:</strong> ${order_data.get('total', 0):.2f} {order_data.get('currency', 'usd').upper()}</p>

            <p>We'll send you another email when your order ships with tracking information.</p>

            <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 14px; color: #666;">
                Best regards,<br>
                The Opportunity Hack Team<br>
                <a href="mailto:questions@ohack.org">questions@ohack.org</a> |
                <a href="https://ohack.dev">ohack.dev</a>
            </p>
            </div>
            """
        }

        resend.Emails.send(params)
        return True
    except Exception as e:
        logger.error("Error sending order confirmation email: %s", str(e))
        return False


def send_admin_order_notification(order_data: Dict[str, Any]) -> bool:
    """Send notification email to admin about new order."""
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
        return False

    resend.api_key = resend_api_key
    order_id = order_data.get('id', '')
    items = order_data.get('items', [])

    try:
        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": ["questions@ohack.org"],
            "subject": f"New Store Order #{order_id[:8]} - ${order_data.get('total', 0):.2f}",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2c5aa0;">New Store Order</h2>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <p><strong>Order ID:</strong> {order_id}</p>
                <p><strong>Customer:</strong> {order_data.get('customerName', '')} ({order_data.get('customerEmail', '')})</p>
                <p><strong>Total:</strong> ${order_data.get('total', 0):.2f} {order_data.get('currency', 'usd').upper()}</p>
                <p><strong>Date:</strong> {order_data.get('timestamp', '')}</p>
            </div>

            {_format_items_html(items)}

            <p><a href="https://ohack.dev/admin/store">View in Admin Panel</a></p>
            </div>
            """
        }

        resend.Emails.send(params)
        return True
    except Exception as e:
        logger.error("Error sending admin order notification: %s", str(e))
        return False


def send_order_status_email(order_data: Dict[str, Any], old_status: str, new_status: str) -> bool:
    """Send status update email to customer."""
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
        return False

    resend.api_key = resend_api_key
    email = order_data.get('customerEmail', '')
    name = order_data.get('customerName', '')
    order_id = order_data.get('id', '')

    status_messages = {
        'processing': 'Your order is now being processed and will ship soon.',
        'shipped': 'Your order has been shipped!',
        'delivered': 'Your order has been delivered. We hope you enjoy it!',
        'cancelled': 'Your order has been cancelled. If you have questions, please contact us.',
    }

    status_message = status_messages.get(new_status, f'Your order status has been updated to: {new_status}.')

    tracking_html = ""
    tracking_number = order_data.get('trackingNumber', '')
    tracking_carrier = order_data.get('trackingCarrier', '')
    if tracking_number and new_status in ('shipped', 'delivered'):
        tracking_html = f"""
        <div style="background-color: #e8f5e9; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h3 style="margin-top: 0;">Tracking Information</h3>
            <p><strong>Carrier:</strong> {tracking_carrier}</p>
            <p><strong>Tracking Number:</strong> {tracking_number}</p>
        </div>"""

    try:
        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "reply_to": "questions@ohack.org",
            "subject": f"Order #{order_id[:8]} - {new_status.capitalize()} - Opportunity Hack Store",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2c5aa0;">Order Update</h2>
            <p>Hello {name},</p>
            <p>{status_message}</p>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <p><strong>Order ID:</strong> {order_id[:8]}</p>
                <p><strong>Status:</strong> {new_status.capitalize()}</p>
            </div>

            {tracking_html}

            <p>If you have any questions about your order, please reply to this email or contact us at questions@ohack.org.</p>

            <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 14px; color: #666;">
                Best regards,<br>
                The Opportunity Hack Team<br>
                <a href="mailto:questions@ohack.org">questions@ohack.org</a> |
                <a href="https://ohack.dev">ohack.dev</a>
            </p>
            </div>
            """
        }

        resend.Emails.send(params)
        return True
    except Exception as e:
        logger.error("Error sending order status email: %s", str(e))
        return False


def send_order_slack_notification(order_data: Dict[str, Any]) -> bool:
    """Send Slack notification to #store-orders channel."""
    order_id = order_data.get('id', '')
    name = order_data.get('customerName', '')
    email = order_data.get('customerEmail', '')
    total = order_data.get('total', 0)
    currency = order_data.get('currency', 'usd').upper()
    items = order_data.get('items', [])

    items_text = "\n".join(
        f"  - {item.get('name', '')} x{item.get('quantity', 1)} = ${item.get('totalPrice', 0):.2f}"
        for item in items
    )

    slack_message = f"""
:shopping_bags: *New Store Order!*
*Order ID:* {order_id[:8]}
*Customer:* {name} ({email})
*Total:* ${total:.2f} {currency}

*Items:*
{items_text}

<https://ohack.dev/admin/store|View in Admin Panel>
"""

    try:
        send_slack(
            message=slack_message,
            channel="store-orders",
            icon_emoji=":shopping_bags:",
            username="Store Bot"
        )
        return True
    except Exception as e:
        logger.error("Error sending Slack notification: %s", str(e))
        return False
