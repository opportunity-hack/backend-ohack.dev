"""
OAuth Provider Utilities

This module provides utilities for handling different OAuth providers (Slack, Google, etc.)
that are configured in PropelAuth.

PropelAuth Configuration:
    Social login providers (Slack, Google, etc.) are configured in the PropelAuth dashboard,
    not in the backend code. Once enabled in PropelAuth, this backend will automatically
    handle authentication tokens from any enabled provider.

User ID Format:
    PropelAuth stores OAuth-based user IDs in the format:
        oauth2|{provider}|{workspace_id}-{user_id}

    Examples:
        - Slack: oauth2|slack|T1Q7936BH-U12345ABC
        - Google: oauth2|google-oauth2|12345678901234567890

    Note: The exact format may vary by provider. Google typically uses 'google-oauth2'
    as the provider identifier.
"""

import re
from common.utils import safe_get_env_var
from common.log import get_logger

logger = get_logger("oauth_providers")

# Default Slack workspace ID from environment or hardcoded fallback
# WARNING: The fallback value T1Q7936BH should match your production Slack workspace
# Set SLACK_WORKSPACE_ID environment variable to override this default
_slack_workspace_id_env = safe_get_env_var("SLACK_WORKSPACE_ID")
if _slack_workspace_id_env != "CHANGEMEPLS":
    DEFAULT_SLACK_WORKSPACE_ID = _slack_workspace_id_env
else:
    # Fallback to hardcoded value - should be configured in production via environment
    DEFAULT_SLACK_WORKSPACE_ID = "T1Q7936BH"
    logger.warning(
        "SLACK_WORKSPACE_ID not configured. Using hardcoded default. "
        "Set SLACK_WORKSPACE_ID environment variable for production."
    )

# OAuth provider patterns
OAUTH_PROVIDER_PATTERN = re.compile(r'^oauth2\|([^|]+)\|(.+)$')
SLACK_PATTERN = re.compile(r'^oauth2\|slack\|([^-]+)-(.+)$')
GOOGLE_PATTERN = re.compile(r'^oauth2\|google-oauth2\|(.+)$')


def get_oauth_provider_from_user_id(user_id):
    """
    Extract the OAuth provider name from a user ID.

    Args:
        user_id: The user ID in format oauth2|provider|identifier

    Returns:
        str: The provider name (e.g., 'slack', 'google-oauth2', None if not OAuth)

    Examples:
        >>> get_oauth_provider_from_user_id('oauth2|slack|T1Q7936BH-U12345')
        'slack'
        >>> get_oauth_provider_from_user_id('oauth2|google-oauth2|1234567890')
        'google-oauth2'
    """
    if not user_id:
        return None

    match = OAUTH_PROVIDER_PATTERN.match(user_id)
    if match:
        return match.group(1)
    return None


def is_slack_user_id(user_id):
    """
    Check if a user ID is from Slack OAuth.

    Args:
        user_id: The user ID to check

    Returns:
        bool: True if the user ID is from Slack, False otherwise
    """
    if not user_id:
        return False
    return user_id.startswith('oauth2|slack|')


def is_google_user_id(user_id):
    """
    Check if a user ID is from Google OAuth.

    Args:
        user_id: The user ID to check

    Returns:
        bool: True if the user ID is from Google, False otherwise
    """
    if not user_id:
        return False
    return user_id.startswith('oauth2|google-oauth2|')


def normalize_slack_user_id(user_id):
    """
    Normalize a Slack user ID to include the oauth2|slack| prefix if missing.

    This is useful for backward compatibility with code that stored raw Slack user IDs
    without the OAuth prefix.

    Args:
        user_id: The user ID, with or without the oauth2|slack| prefix

    Returns:
        str: The normalized user ID with the oauth2|slack|{workspace_id}- prefix

    Examples:
        >>> normalize_slack_user_id('U12345ABC')
        'oauth2|slack|T1Q7936BH-U12345ABC'
        >>> normalize_slack_user_id('oauth2|slack|T1Q7936BH-U12345ABC')
        'oauth2|slack|T1Q7936BH-U12345ABC'
    """
    if not user_id:
        return user_id

    # Already has the full OAuth prefix (any provider) - don't modify
    if user_id.startswith('oauth2|'):
        return user_id

    # Add the Slack prefix with workspace ID
    prefix = f"oauth2|slack|{DEFAULT_SLACK_WORKSPACE_ID}-"
    return f"{prefix}{user_id}"


def extract_slack_user_id(user_id):
    """
    Extract the raw Slack user ID from a full OAuth user ID.

    Args:
        user_id: The full user ID in format oauth2|slack|T1Q7936BH-U12345ABC

    Returns:
        str: The raw Slack user ID (e.g., 'U12345ABC'), or original if not Slack format

    Examples:
        >>> extract_slack_user_id('oauth2|slack|T1Q7936BH-U12345ABC')
        'U12345ABC'
        >>> extract_slack_user_id('oauth2|google-oauth2|1234567890')
        'oauth2|google-oauth2|1234567890'
    """
    if not user_id:
        return user_id

    match = SLACK_PATTERN.match(user_id)
    if match:
        return match.group(2)  # Returns the user ID part after workspace-

    return user_id


def get_provider_display_name(user_id):
    """
    Get a human-readable display name for the OAuth provider.

    Args:
        user_id: The user ID containing the provider information

    Returns:
        str: Display name for the provider (e.g., 'Slack', 'Google', 'Unknown')
    """
    provider = get_oauth_provider_from_user_id(user_id)

    if not provider:
        return "Unknown"

    provider_names = {
        'slack': 'Slack',
        'google-oauth2': 'Google',
        'github': 'GitHub',
        'microsoft': 'Microsoft'
    }

    return provider_names.get(provider, provider.title())


def is_oauth_user_id(user_id):
    """
    Check if a user ID is from any OAuth provider.

    Args:
        user_id: The user ID to check

    Returns:
        bool: True if the user ID is from an OAuth provider, False otherwise
    """
    if not user_id:
        return False
    return user_id.startswith('oauth2|')


# Backward compatibility: Keep the old constant name
USER_ID_PREFIX = f"oauth2|slack|{DEFAULT_SLACK_WORKSPACE_ID}-"
SLACK_PREFIX = f"oauth2|slack|{DEFAULT_SLACK_WORKSPACE_ID}-"
GOOGLE_PREFIX = "oauth2|google-oauth2|"


def get_oauth_provider_from_propel_response(propel_response):
    """
    Detect which OAuth provider was used from a PropelAuth oauth_token response.

    PropelAuth returns OAuth tokens in a format like:
        {'slack': {'access_token': '...', ...}}
        {'google': {'access_token': '...', ...}}

    Args:
        propel_response: The JSON response from PropelAuth's oauth_token endpoint

    Returns:
        tuple: (provider_name, token_data) or (None, None) if not found

    Examples:
        >>> get_oauth_provider_from_propel_response({'slack': {'access_token': 'xoxp-...'}})
        ('slack', {'access_token': 'xoxp-...'})
        >>> get_oauth_provider_from_propel_response({'google': {'access_token': 'ya29...'}})
        ('google', {'access_token': 'ya29...'})
    """
    if not propel_response:
        return None, None

    # Check for known providers in order of likelihood
    known_providers = ['slack', 'google', 'github', 'microsoft']

    for provider in known_providers:
        if provider in propel_response:
            return provider, propel_response[provider]

    # Check for any other provider
    for key, value in propel_response.items():
        if isinstance(value, dict) and 'access_token' in value:
            return key, value

    return None, None


def build_user_id_for_provider(provider, provider_user_id, workspace_id=None):
    """
    Build a normalized user ID for a given OAuth provider.

    Args:
        provider: The OAuth provider name ('slack', 'google', etc.)
        provider_user_id: The user ID from the provider
        workspace_id: For Slack, the workspace ID (optional, uses default if not provided)

    Returns:
        str: The normalized user ID in format oauth2|provider|identifier

    Examples:
        >>> build_user_id_for_provider('slack', 'U12345', 'T1Q7936BH')
        'oauth2|slack|T1Q7936BH-U12345'
        >>> build_user_id_for_provider('google', '1234567890')
        'oauth2|google-oauth2|1234567890'
    """
    if provider == 'slack':
        ws_id = workspace_id or DEFAULT_SLACK_WORKSPACE_ID
        return f"oauth2|slack|{ws_id}-{provider_user_id}"
    elif provider == 'google':
        return f"oauth2|google-oauth2|{provider_user_id}"
    elif provider == 'github':
        return f"oauth2|github|{provider_user_id}"
    elif provider == 'microsoft':
        return f"oauth2|microsoft|{provider_user_id}"
    else:
        return f"oauth2|{provider}|{provider_user_id}"
