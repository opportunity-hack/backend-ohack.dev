from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from common.log import get_logger
from common.utils.slack import (
    get_client, userlist, get_user_info, rate_limited_get_user_info, presence
)
from common.utils.redis_cache import redis_cached, clear_pattern

logger = get_logger(__name__)

@redis_cached(prefix="slack:active_users", ttl=10)  # Cache for 10 seconds
def get_active_users(days: int = 30, include_presence: bool = False, minimum_presence: str = None) -> List[Dict[str, Any]]:
    """
    Get active Slack users based on their activity within the specified time period.
    
    Args:
        days: Number of days to look back for activity (default: 30)
        include_presence: Whether to include current presence information (default: False)
        minimum_presence: Filter by minimum presence status ('active' or 'away')
        
    Returns:
        List of active users with relevant information
    """
    logger.info(f"Fetching active Slack users within the last {days} days")
    
    # Get the cutoff timestamp
    cutoff_time = datetime.now() - timedelta(days=days)
    
    # Get all users
    response = userlist()
    if not response or "members" not in response:
        logger.error("Failed to retrieve Slack users list")
        return []
    
    active_users = []
    for member in response["members"]:
        # Skip bots, deleted users, and restricted accounts
        if (
            member.get("is_bot", False) or 
            member.get("deleted", False) or 
            member.get("is_restricted", False) or 
            member.get("is_ultra_restricted", False) or
            member.get("name", "").startswith("slackbot")
        ):
            continue
        
        # Check if the user has been active within the specified time period
        updated_timestamp = member.get("updated", 0)
        updated_date = datetime.fromtimestamp(updated_timestamp)
        
        if updated_date >= cutoff_time:
            user_info = {
                "id": member["id"],
                "name": member["name"],
                "real_name": member.get("real_name", ""),
                "display_name": member["profile"].get("display_name", ""),
                # "email": member["profile"].get("email", ""),
                "title": member["profile"].get("title", ""),
                "last_active": updated_date.isoformat(),
                "is_admin": member.get("is_admin", False),
                "tz": member.get("tz", "")
            }
            
            # Add presence information if requested
            if include_presence and not member.get("deleted", False):
                try:
                    user_presence = presence(user_id=member["id"])
                    user_info["presence"] = user_presence.get("presence", "unknown")
                    print(f"User presence: {user_presence}")
                    
                    # Filter by minimum presence if specified
                    if minimum_presence and user_info.get("presence") != minimum_presence:
                        continue
                except Exception as e:
                    logger.warning(f"Failed to get presence for user {member['id']}: {str(e)}")
                    user_info["presence"] = "unknown"
            
            active_users.append(user_info)
    
    logger.info(f"Found {len(active_users)} active users")
    return active_users

@redis_cached(prefix="slack:user_details", ttl=10)  # Cache for 10 seconds
def get_user_details(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about a specific Slack user.
    
    Args:
        user_id: Slack user ID
        
    Returns:
        Dictionary with user details or None if not found
    """
    logger.info(f"Fetching details for Slack user {user_id}")
    
    try:
        user_info = rate_limited_get_user_info(user_id)
        if not user_info:
            logger.warning(f"No information found for user {user_id}")
            return None
        
        # Get presence information
        user_presence = presence(user_id=user_id)
        
        # Format the response
        result = {
            "id": user_info["id"],
            "name": user_info["name"],
            "real_name": user_info.get("real_name", ""),
            "display_name": user_info["profile"].get("display_name", ""),
            "email": user_info["profile"].get("email", ""),
            "title": user_info["profile"].get("title", ""),
            "phone": user_info["profile"].get("phone", ""),
            "image": user_info["profile"].get("image_192", ""),
            "status_text": user_info["profile"].get("status_text", ""),
            "status_emoji": user_info["profile"].get("status_emoji", ""),
            "presence": user_presence.get("presence", "unknown"),
            "updated": user_info.get("updated", 0),
            "is_admin": user_info.get("is_admin", False),
            "is_owner": user_info.get("is_owner", False),
            "tz": user_info.get("tz", ""),
            "tz_offset": user_info.get("tz_offset", 0)
        }
        
        return result
    except Exception as e:
        logger.error(f"Error fetching details for user {user_id}: {str(e)}")
        return None

def clear_slack_cache() -> Dict[str, Any]:
    """
    Clear all Slack-related caches
    
    Returns:
        Status of the cache clearing operation
    """
    logger.info("Clearing all Slack caches")
    
    # Clear all Slack caches
    active_users_cleared = clear_pattern("slack:active_users:*")
    user_details_cleared = clear_pattern("slack:user_details:*")
    
    return {
        "success": active_users_cleared and user_details_cleared,
        "message": "All Slack caches cleared successfully"
    }