from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from common.log import get_logger, info, warning, error
from common.utils.slack import (
    get_client, userlist, get_user_info, rate_limited_get_user_info, presence
)
from common.utils.redis_cache import redis_cached, clear_pattern
from common.utils.oauth_providers import normalize_slack_user_id
from db.db import fetch_user_by_user_id
from services.users_service import save_user

logger = get_logger(__name__)

@redis_cached(prefix="slack:active_users", ttl=10)  # Cache for 10 seconds
def get_active_users(days: int = 30, include_presence: bool = False, minimum_presence: str = None, admin: bool = False) -> List[Dict[str, Any]]:
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
                "title": member["profile"].get("title", ""),
                "last_active": updated_date.isoformat(),
                "is_admin": member.get("is_admin", False),
                "tz": member.get("tz", "")
            }
            # If admin, add email
            if admin:
                user_info["email"] = member["profile"].get("email", "")

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


def sync_slack_users_to_firestore(lookback_days: int = 30) -> Dict[str, Any]:
    """
    Sync Slack workspace users into Firestore. Creates user records for
    recently active Slack members who don't already have a Firestore user
    with a Slack-based user_id. This ensures give_hearts_to_user() can
    find users by Slack ID even if they never logged in via Slack SSO.

    Args:
        lookback_days: Only process members whose Slack profile was updated
                       within this many days (default: 30)

    Returns:
        Summary dict with counts of created, skipped, and errored users
    """
    info(logger, "Starting Slack user sync", lookback_days=lookback_days)

    cutoff_time = datetime.now() - timedelta(days=lookback_days)

    response = userlist()
    if not response or "members" not in response:
        error(logger, "Failed to retrieve Slack users list")
        return {
            "total_slack_members": 0,
            "filtered_by_lookback": 0,
            "created": 0,
            "skipped_existing": 0,
            "skipped_no_email": 0,
            "errors": [{"reason": "Failed to retrieve Slack users list"}],
        }

    all_members = response["members"]
    total_slack_members = len(all_members)

    created = 0
    skipped_existing = 0
    skipped_no_email = 0
    filtered_by_lookback = 0
    errors = []

    for member in all_members:
        # Skip bots, deleted users, and restricted accounts (same filter as get_active_users)
        if (
            member.get("is_bot", False)
            or member.get("deleted", False)
            or member.get("is_restricted", False)
            or member.get("is_ultra_restricted", False)
            or member.get("name", "").startswith("slackbot")
        ):
            continue

        # Filter by updated timestamp
        updated_timestamp = member.get("updated", 0)
        updated_date = datetime.fromtimestamp(updated_timestamp)
        if updated_date < cutoff_time:
            continue

        filtered_by_lookback += 1
        raw_slack_id = member["id"]
        normalized_user_id = normalize_slack_user_id(raw_slack_id)

        # Check if user already exists in Firestore
        existing_user = fetch_user_by_user_id(normalized_user_id)
        if existing_user is not None:
            skipped_existing += 1
            continue

        # Check for email
        email = member.get("profile", {}).get("email", "")
        if not email:
            skipped_no_email += 1
            continue

        # Extract profile data
        profile = member.get("profile", {})
        profile_image = profile.get("image_192", "")
        name = member.get("real_name", member.get("name", ""))
        nickname = profile.get("display_name", member.get("name", ""))
        last_login = datetime.utcnow().isoformat() + "Z"

        try:
            result = save_user(
                user_id=normalized_user_id,
                email=email,
                last_login=last_login,
                profile_image=profile_image,
                name=name,
                nickname=nickname,
                propel_id=None,
            )
            if result is not None:
                created += 1
                info(logger, "Created user from Slack sync",
                     slack_id=raw_slack_id, email=email, name=name)
            else:
                errors.append({
                    "slack_id": raw_slack_id,
                    "name": name,
                    "reason": "save_user returned None",
                })
        except Exception as e:
            warning(logger, "Failed to create user during Slack sync",
                    slack_id=raw_slack_id, name=name, error=str(e))
            errors.append({
                "slack_id": raw_slack_id,
                "name": name,
                "reason": str(e),
            })

    summary = {
        "total_slack_members": total_slack_members,
        "filtered_by_lookback": filtered_by_lookback,
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_no_email": skipped_no_email,
        "errors": errors,
    }
    info(logger, "Slack user sync complete", **{k: v for k, v in summary.items() if k != "errors"})
    return summary