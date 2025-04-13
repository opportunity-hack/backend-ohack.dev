import logging
from flask import Blueprint, jsonify
from api.leaderboard.leaderboard_service import get_github_leaderboard

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)

# Blueprint configuration
BP_NAME = 'api-leaderboard'
BP_URL_PREFIX = '/api/leaderboard'
bp = Blueprint(BP_NAME, __name__, url_prefix=BP_URL_PREFIX)

@bp.route("/<event_id>", methods=["GET"])
def get_leaderboard_by_event_id(event_id):
    """
    Get GitHub leaderboard data for a specific event.
    
    Args:
        event_id: The event ID to get leaderboard data for.
        
    Returns:
        JSON response with GitHub organizations, repositories, and contributors.
    """
    try:
        logger.info("Getting leaderboard data for event ID: %s", event_id)
        leaderboard_data = get_github_leaderboard(event_id)
        return jsonify(leaderboard_data)
    except Exception as e:
        logger.error("Error getting leaderboard data: %s", str(e))
        return jsonify({"error": str(e)}), 500
