from flask import Blueprint, jsonify, request
from api.slack.slack_service import (
    get_active_users, get_user_details, clear_slack_cache
)
from common.auth import auth
from common.exceptions import AuthorizationError, ValidationError
from common.log import get_logger
from common.utils.slack import send_slack

logger = get_logger(__name__)
bp = Blueprint('slack', __name__, url_prefix='/api')

@bp.route("/slack/users/active", methods=["GET"])
@auth.require_user
def active_users():
    """
    API endpoint to get active Slack users within a specified time period.
    
    Query parameters:
        days: Number of days to look back for activity (default: 30)
        include_presence: Whether to include current presence information (default: false)
        minimum_presence: Filter by minimum presence status ('active' or 'away')
        
    Returns:
        JSON response with list of active users
    """
    try:
        # Parse query parameters
        days = request.args.get('active_days', default=30, type=int)
        if days < 1 or days > 365:
            raise ValidationError("active_days parameter must be between 1 and 365")
        
        include_presence = request.args.get('include_presence', default='false', type=str).lower() == 'true'
        minimum_presence = request.args.get('minimum_presence', default=None, type=str)
        
        # Validate minimum_presence parameter
        if minimum_presence and minimum_presence not in ['active', 'away']:
            raise ValidationError("Minimum presence must be either 'active' or 'away'")
        
        # Get active users
        users = get_active_users(days=days, include_presence=include_presence, minimum_presence=minimum_presence)
        
        return jsonify({
            "success": True,
            "count": len(users),
            "users": users
        })
    
    except ValidationError as e:
        logger.warning("ValidationError: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
    
    except Exception as e:
        logger.error("Error retrieving active Slack users: %s", str(e))
        return jsonify({
            "success": False,
            "error": "Failed to retrieve active Slack users"
        }), 500

@bp.route("/slack/users/<user_id>", methods=["GET"])
@auth.require_user
def user_details(user_id):
    """
    API endpoint to get detailed information about a specific Slack user.
    
    Args:
        user_id: Slack user ID
        
    Returns:
        JSON response with user details
    """
    try:
        # Get user details
        user = get_user_details(user_id)
        
        if not user:
            return jsonify({
                "success": False,
                "error": f"User with ID {user_id} not found"
            }), 404
        
        return jsonify({
            "success": True,
            "user": user
        })
    
    except Exception as e:
        logger.error("Error retrieving details for Slack user %s: %s", 
                     user_id, str(e))
        return jsonify({
            "success": False,
            "error": "Failed to retrieve details for Slack user " + user_id
        }), 500

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

@bp.route("/slack/cache/clear", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def clear_cache():
    """
    API endpoint to clear all Slack-related caches.
    Requires admin scope.
    
    Returns:
        JSON response with operation status
    """
    try:
        result = clear_slack_cache()
        
        return jsonify({
            "success": result["success"],
            "message": result["message"]
        })
    
    except AuthorizationError as e:
        logger.warning("Authorization error: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e)
        }), 403
    
    except Exception as e:
        logger.error("Error clearing Slack cache: %s", str(e))
        return jsonify({
            "success": False,
            "error": "Failed to clear Slack cache"
        }), 500

@bp.route("/slack/message", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("volunteer.admin", 
                                          req_to_org_id=getOrgId)
def send_message():
    """
    API endpoint to send a message to a Slack channel or user.
    Requires admin scope.

    Request body:
        message: The message to send (required)
        channel: The channel name or user ID to send to (required)
        username: Custom username for the bot (optional,
                 defaults to "Hackathon Bot")
        icon_emoji: Custom emoji for the bot (optional)

    Returns:
        JSON response with operation status
    """
    try:
        request_data = request.get_json()
        if not request_data:
            return jsonify({
                "success": False,
                "error": "Missing request body"
            }), 400

        message = request_data.get('message')
        channel = request_data.get('channel')
        username = request_data.get('username', 'Hackathon Bot')
        icon_emoji = request_data.get('icon_emoji')

        if not message:
            return jsonify({
                "success": False,
                "error": "Message is required"
            }), 400

        if not channel:
            return jsonify({
                "success": False,
                "error": "Channel is required"
            }), 400

        # Send the Slack message
        send_slack(
            message=message,
            channel=channel,
            username=username,
            icon_emoji=icon_emoji
        )

        return jsonify({
            "success": True,
            "message": "Message sent successfully"
        })

    except AuthorizationError as e:
        logger.warning("Authorization error: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e)
        }), 403

    except Exception as e:
        logger.error("Error sending Slack message: %s", str(e))
        return jsonify({
            "success": False,
            "error": "Failed to send message"
        }), 500
