import re
from common.log import get_logger
from common.utils.slack import get_channel_id_from_channel_name
from common.utils.github import validate_github_username

logger = get_logger(__name__)

def validate_slack_channel(channel_name):
    """
    Validate if a Slack channel name is valid and if it exists
    
    Args:
        channel_name: Name of the Slack channel to validate
        
    Returns:
        Dictionary with validation results
    """
    # Check if channel name is valid according to Slack's naming requirements
    if not re.match(r'^[a-z0-9_-]{1,80}$', channel_name):
        logger.info(f"Invalid Slack channel name format: {channel_name}")
        return {
            "valid": False,
            "message": "Invalid channel name. Must be lowercase, no spaces, and only contain letters, numbers, hyphens, and underscores."
        }
    
    # Make sure the channel isn't general, ask-a-mentor, or anything with the current year YYYY-summmer, spring, fall, or winter
    if channel_name in ["general", "ask-a-mentor"] or re.search(r'\d{4}-(summer|spring|fall|winter)', channel_name):
        logger.info(f"Slack channel name is reserved or invalid: {channel_name}")
        return {
            "valid": False,
            "message": "Channel name is reserved or invalid. Please choose a different name."
        }

    # Check if channel already exists
    try:
        channel_id = get_channel_id_from_channel_name(channel_name)
        if channel_id:
            logger.info(f"Slack channel exists: {channel_name}")
            return {
                "valid": True,
                "exists": True, # Don't block creation if it exists (used to be True)
                "message": f"Channel '{channel_name}' already exists."
            }
        else:
            logger.info(f"Slack channel is available: {channel_name}")
            return {
                "valid": True, 
                "exists": False,
                "message": f"Channel '{channel_name}' is available to be created."
            }
    except Exception as e:
        logger.error(f"Error validating Slack channel: {str(e)}")
        return {
            "valid": False,
            "message": f"Error validating channel name: {str(e)}"
        }

def validate_github_user(username):
    """
    Validate if a GitHub username exists
    
    Args:
        username: GitHub username to validate
        
    Returns:
        Dictionary with validation results
    """
    try:
        is_valid = validate_github_username(username)
        if is_valid:
            logger.info(f"GitHub username exists: {username}")
            return {
                "valid": True,
                "message": f"GitHub username '{username}' exists."
            }
        else:
            logger.info(f"GitHub username does not exist: {username}")
            return {
                "valid": False,
                "message": f"GitHub username '{username}' does not exist."
            }
    except Exception as e:
        logger.error(f"Error validating GitHub username: {str(e)}")
        return {
            "valid": False,
            "message": f"Error validating GitHub username: {str(e)}"
        }