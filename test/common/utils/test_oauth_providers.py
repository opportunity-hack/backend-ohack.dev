"""
Unit tests for OAuth provider utilities
"""
import pytest
from common.utils.oauth_providers import (
    get_oauth_provider_from_user_id,
    is_slack_user_id,
    is_google_user_id,
    is_oauth_user_id,
    normalize_slack_user_id,
    extract_slack_user_id,
    get_provider_display_name,
    get_oauth_provider_from_propel_response,
    build_user_id_for_provider,
    DEFAULT_SLACK_WORKSPACE_ID  # Import the constant for testing
)

# Test constant - matches the default workspace ID
TEST_SLACK_WORKSPACE_ID = DEFAULT_SLACK_WORKSPACE_ID


class TestOAuthProviderDetection:
    """Tests for OAuth provider detection functions"""

    def test_get_oauth_provider_from_slack_user_id(self):
        """Test extracting provider from Slack user ID"""
        user_id = f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC"
        assert get_oauth_provider_from_user_id(user_id) == "slack"

    def test_get_oauth_provider_from_google_user_id(self):
        """Test extracting provider from Google user ID"""
        user_id = "oauth2|google-oauth2|1234567890123456789"
        assert get_oauth_provider_from_user_id(user_id) == "google-oauth2"

    def test_get_oauth_provider_from_github_user_id(self):
        """Test extracting provider from GitHub user ID"""
        user_id = "oauth2|github|12345678"
        assert get_oauth_provider_from_user_id(user_id) == "github"

    def test_get_oauth_provider_from_non_oauth_user_id(self):
        """Test with non-OAuth user ID"""
        user_id = "regular-user-id-123"
        assert get_oauth_provider_from_user_id(user_id) is None

    def test_get_oauth_provider_from_none(self):
        """Test with None user ID"""
        assert get_oauth_provider_from_user_id(None) is None

    def test_get_oauth_provider_from_empty_string(self):
        """Test with empty string"""
        assert get_oauth_provider_from_user_id("") is None


class TestSlackUserIdDetection:
    """Tests for Slack user ID detection"""

    def test_is_slack_user_id_positive(self):
        """Test identifying Slack user IDs"""
        assert is_slack_user_id(f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC") is True

    def test_is_slack_user_id_negative_google(self):
        """Test rejecting Google user IDs"""
        assert is_slack_user_id("oauth2|google-oauth2|1234567890") is False

    def test_is_slack_user_id_negative_none(self):
        """Test with None"""
        assert is_slack_user_id(None) is False

    def test_is_slack_user_id_negative_empty(self):
        """Test with empty string"""
        assert is_slack_user_id("") is False


class TestGoogleUserIdDetection:
    """Tests for Google user ID detection"""

    def test_is_google_user_id_positive(self):
        """Test identifying Google user IDs"""
        assert is_google_user_id("oauth2|google-oauth2|1234567890123456789") is True

    def test_is_google_user_id_negative_slack(self):
        """Test rejecting Slack user IDs"""
        assert is_google_user_id("oauth2|slack|T1Q7936BH-U12345ABC") is False

    def test_is_google_user_id_negative_none(self):
        """Test with None"""
        assert is_google_user_id(None) is False


class TestOAuthUserIdDetection:
    """Tests for general OAuth user ID detection"""

    def test_is_oauth_user_id_slack(self):
        """Test with Slack OAuth user ID"""
        assert is_oauth_user_id(f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC") is True

    def test_is_oauth_user_id_google(self):
        """Test with Google OAuth user ID"""
        assert is_oauth_user_id("oauth2|google-oauth2|1234567890") is True

    def test_is_oauth_user_id_github(self):
        """Test with GitHub OAuth user ID"""
        assert is_oauth_user_id("oauth2|github|12345678") is True

    def test_is_oauth_user_id_negative(self):
        """Test with non-OAuth user ID"""
        assert is_oauth_user_id("regular-user-id") is False

    def test_is_oauth_user_id_none(self):
        """Test with None"""
        assert is_oauth_user_id(None) is False


class TestSlackUserIdNormalization:
    """Tests for Slack user ID normalization"""

    def test_normalize_slack_user_id_raw(self):
        """Test normalizing raw Slack user ID"""
        result = normalize_slack_user_id("U12345ABC")
        assert result == f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC"

    def test_normalize_slack_user_id_already_normalized(self):
        """Test with already normalized Slack user ID"""
        user_id = f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC"
        result = normalize_slack_user_id(user_id)
        assert result == user_id

    def test_normalize_slack_user_id_google(self):
        """Test with Google user ID (should not modify)"""
        user_id = "oauth2|google-oauth2|1234567890"
        result = normalize_slack_user_id(user_id)
        assert result == user_id

    def test_normalize_slack_user_id_none(self):
        """Test with None"""
        assert normalize_slack_user_id(None) is None

    def test_normalize_slack_user_id_empty(self):
        """Test with empty string"""
        assert normalize_slack_user_id("") == ""


class TestSlackUserIdExtraction:
    """Tests for extracting raw Slack user ID"""

    def test_extract_slack_user_id_from_full(self):
        """Test extracting raw Slack user ID from full format"""
        user_id = f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC"
        result = extract_slack_user_id(user_id)
        assert result == "U12345ABC"

    def test_extract_slack_user_id_from_different_workspace(self):
        """Test extracting from different workspace ID"""
        user_id = "oauth2|slack|T9999999-U67890XYZ"
        result = extract_slack_user_id(user_id)
        assert result == "U67890XYZ"

    def test_extract_slack_user_id_from_google(self):
        """Test with Google user ID (should return as-is)"""
        user_id = "oauth2|google-oauth2|1234567890"
        result = extract_slack_user_id(user_id)
        assert result == user_id

    def test_extract_slack_user_id_from_raw(self):
        """Test with raw user ID (should return as-is)"""
        user_id = "U12345ABC"
        result = extract_slack_user_id(user_id)
        assert result == user_id

    def test_extract_slack_user_id_none(self):
        """Test with None"""
        assert extract_slack_user_id(None) is None


class TestProviderDisplayName:
    """Tests for provider display name generation"""

    def test_get_provider_display_name_slack(self):
        """Test display name for Slack"""
        user_id = f"oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC"
        assert get_provider_display_name(user_id) == "Slack"

    def test_get_provider_display_name_google(self):
        """Test display name for Google"""
        user_id = "oauth2|google-oauth2|1234567890"
        assert get_provider_display_name(user_id) == "Google"

    def test_get_provider_display_name_github(self):
        """Test display name for GitHub"""
        user_id = "oauth2|github|12345678"
        assert get_provider_display_name(user_id) == "GitHub"

    def test_get_provider_display_name_microsoft(self):
        """Test display name for Microsoft"""
        user_id = "oauth2|microsoft|uuid-here"
        assert get_provider_display_name(user_id) == "Microsoft"

    def test_get_provider_display_name_unknown(self):
        """Test display name for unknown provider"""
        user_id = "oauth2|unknown-provider|12345"
        # Should title-case the provider name
        assert get_provider_display_name(user_id) == "Unknown-Provider"

    def test_get_provider_display_name_non_oauth(self):
        """Test display name for non-OAuth user ID"""
        user_id = "regular-user-id"
        assert get_provider_display_name(user_id) == "Unknown"


class TestPropelResponseProviderDetection:
    """Tests for detecting OAuth provider from PropelAuth response"""

    def test_detect_slack_provider(self):
        """Test detecting Slack from PropelAuth response"""
        response = {
            'slack': {
                'access_token': 'xoxp-123456',
                'refresh_token': None,
                'token_provider': 'slack'
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider == 'slack'
        assert token_data['access_token'] == 'xoxp-123456'

    def test_detect_google_provider(self):
        """Test detecting Google from PropelAuth response"""
        response = {
            'google': {
                'access_token': 'ya29.abc123',
                'refresh_token': None,
                'token_provider': 'google',
                'token_expiration': 1770096897,
                'authorized_scopes': ['openid', 'email', 'profile']
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider == 'google'
        assert token_data['access_token'] == 'ya29.abc123'

    def test_detect_github_provider(self):
        """Test detecting GitHub from PropelAuth response"""
        response = {
            'github': {
                'access_token': 'gho_abc123',
                'refresh_token': None
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider == 'github'
        assert token_data['access_token'] == 'gho_abc123'

    def test_detect_microsoft_provider(self):
        """Test detecting Microsoft from PropelAuth response"""
        response = {
            'microsoft': {
                'access_token': 'EwBwA...',
                'refresh_token': None
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider == 'microsoft'
        assert token_data['access_token'] == 'EwBwA...'

    def test_empty_response(self):
        """Test with empty response"""
        provider, token_data = get_oauth_provider_from_propel_response({})
        assert provider is None
        assert token_data is None

    def test_none_response(self):
        """Test with None response"""
        provider, token_data = get_oauth_provider_from_propel_response(None)
        assert provider is None
        assert token_data is None

    def test_unknown_provider_with_access_token(self):
        """Test with unknown provider that has access_token structure"""
        response = {
            'custom_oauth': {
                'access_token': 'custom_token_123'
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider == 'custom_oauth'
        assert token_data['access_token'] == 'custom_token_123'

    def test_response_without_access_token(self):
        """Test with response that doesn't have access_token in nested dict"""
        response = {
            'something': {
                'other_field': 'value'
            }
        }
        provider, token_data = get_oauth_provider_from_propel_response(response)
        assert provider is None
        assert token_data is None


class TestBuildUserIdForProvider:
    """Tests for building normalized user IDs for different providers"""

    def test_build_slack_user_id_with_workspace(self):
        """Test building Slack user ID with explicit workspace"""
        result = build_user_id_for_provider('slack', 'U12345ABC', 'T9999999')
        assert result == 'oauth2|slack|T9999999-U12345ABC'

    def test_build_slack_user_id_default_workspace(self):
        """Test building Slack user ID with default workspace"""
        result = build_user_id_for_provider('slack', 'U12345ABC')
        assert result == f'oauth2|slack|{TEST_SLACK_WORKSPACE_ID}-U12345ABC'

    def test_build_google_user_id(self):
        """Test building Google user ID"""
        result = build_user_id_for_provider('google', '1234567890123456789')
        assert result == 'oauth2|google-oauth2|1234567890123456789'

    def test_build_github_user_id(self):
        """Test building GitHub user ID"""
        result = build_user_id_for_provider('github', '12345678')
        assert result == 'oauth2|github|12345678'

    def test_build_microsoft_user_id(self):
        """Test building Microsoft user ID"""
        result = build_user_id_for_provider('microsoft', 'uuid-here')
        assert result == 'oauth2|microsoft|uuid-here'

    def test_build_unknown_provider_user_id(self):
        """Test building user ID for unknown provider"""
        result = build_user_id_for_provider('custom_provider', 'user123')
        assert result == 'oauth2|custom_provider|user123'
