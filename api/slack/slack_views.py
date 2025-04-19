from flask import Blueprint, jsonify, request
from common.log import get_logger
from api.slack.slack_service import (
    get_active_users, get_user_details, clear_slack_cache
)
from common.auth import auth, auth_user
from common.exceptions import AuthorizationError, ValidationError

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
        logger.warning(f"ValidationError: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
    
    except Exception as e:
        logger.error(f"Error retrieving active Slack users: {str(e)}")
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
        logger.error(f"Error retrieving details for Slack user {user_id}: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Failed to retrieve details for Slack user {user_id}"
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
        logger.warning(f"Authorization error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 403
    
    except Exception as e:
        logger.error(f"Error clearing Slack cache: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Failed to clear Slack cache"
        }), 500