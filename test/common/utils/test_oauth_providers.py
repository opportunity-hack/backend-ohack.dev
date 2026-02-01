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
