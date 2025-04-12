from flask import Blueprint, jsonify
from common.log import get_logger
from api.validate.validate_service import validate_slack_channel, validate_github_user

logger = get_logger(__name__)
bp = Blueprint('validate', __name__, url_prefix='/api')

@bp.route("/validate/slack/<channel>", methods=["GET"])
def validate_slack(channel):
    """
    API endpoint to validate if a Slack channel name is valid and exists
    
    Args:
        channel: Slack channel name to validate
        
    Returns:
        JSON response with validation result
    """
    result = validate_slack_channel(channel)
    status_code = 200 if result.get("valid", False) else 400
    
    # If not valid but we have a specific reason (like GitHub API error), use 500 instead
    if not result.get("valid", False) and "Error validating" in result.get("message", ""):
        status_code = 500
        
    return jsonify(result), status_code

@bp.route("/validate/github/<username>", methods=["GET"])
def validate_github(username):
    """
    API endpoint to validate if a GitHub username exists
    
    Args:
        username: GitHub username to validate
        
    Returns:
        JSON response with validation result
    """
    result = validate_github_user(username)
    
    # Determine the status code based on the validation result
    if result.get("valid", False):
        status_code = 200
    elif "does not exist" in result.get("message", ""):
        status_code = 404
    else:
        status_code = 500
        
    return jsonify(result), status_code