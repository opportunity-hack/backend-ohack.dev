import pytest
from unittest.mock import patch, MagicMock
import datetime
from api.slack.slack_service import get_active_users, get_user_details, clear_slack_cache

@pytest.fixture
def mock_userlist_response():
    return {
        "members": [
            {
                "id": "U123456",
                "name": "testuser1",
                "real_name": "Test User 1",
                "profile": {
                    "display_name": "testuser1",
                    "email": "testuser1@example.com",
                    "title": "Developer"
                },
                "updated": int((datetime.datetime.now() - datetime.timedelta(days=5)).timestamp()),
                "is_admin": True,
                "is_bot": False,
                "deleted": False,
                "tz": "America/Los_Angeles"
            },
            {
                "id": "U234567",
                "name": "testuser2",
                "real_name": "Test User 2",
                "profile": {
                    "display_name": "testuser2",
                    "email": "testuser2@example.com",
                    "title": "Designer"
                },
                "updated": int((datetime.datetime.now() - datetime.timedelta(days=35)).timestamp()),
                "is_admin": False,
                "is_bot": False,
                "deleted": False,
                "tz": "America/New_York"
            },
            {
                "id": "B345678",
                "name": "testbot",
                "real_name": "Test Bot",
                "profile": {
                    "display_name": "testbot",
                    "email": "testbot@example.com"
                },
                "updated": int((datetime.datetime.now() - datetime.timedelta(days=1)).timestamp()),
                "is_admin": False,
                "is_bot": True,
                "deleted": False,
                "tz": "UTC"
            },
            {
                "id": "U456789",
                "name": "deleted_user",
                "real_name": "Deleted User",
                "profile": {
                    "display_name": "deleted_user",
                    "email": "deleted@example.com",
                    "title": "Former Employee"
                },
                "updated": int((datetime.datetime.now() - datetime.timedelta(days=10)).timestamp()),
                "is_admin": False,
                "is_bot": False,
                "deleted": True,
                "tz": "Europe/London"
            }
        ]
    }

@pytest.fixture
def mock_presence_response():
    return {"presence": "active"}

@pytest.fixture
def mock_user_info_response():
    return {
        "id": "U123456",
        "name": "testuser",
        "real_name": "Test User",
        "profile": {
            "display_name": "testuser",
            "email": "testuser@example.com",
            "title": "Developer",
            "phone": "123-456-7890",
            "image_192": "https://example.com/image.jpg",
            "status_text": "Working remotely",
            "status_emoji": ":house:"
        },
        "updated": int(datetime.datetime.now().timestamp()),
        "is_admin": True,
        "is_owner": False,
        "tz": "America/Los_Angeles",
        "tz_offset": -28800
    }

@patch('api.slack.slack_service.userlist')
@patch('api.slack.slack_service.presence')
def test_get_active_users_default_params(mock_presence, mock_userlist, mock_userlist_response, mock_presence_response):
    # Setup
    mock_userlist.return_value = mock_userlist_response
    mock_presence.return_value = mock_presence_response
    
    # Execute
    result = get_active_users()
    
    # Assert
    assert len(result) == 1  # Only testuser1 is within 30 days and not a bot or deleted
    assert result[0]["id"] == "U123456"
    assert result[0]["name"] == "testuser1"
    assert result[0]["real_name"] == "Test User 1"
    assert "presence" not in result[0]  # Presence not included by default
    
    # Verify mocks
    mock_userlist.assert_called_once()
    mock_presence.assert_not_called()

@patch('api.slack.slack_service.userlist')
@patch('api.slack.slack_service.presence')
def test_get_active_users_with_presence(mock_presence, mock_userlist, mock_userlist_response, mock_presence_response):
    # Setup
    mock_userlist.return_value = mock_userlist_response
    mock_presence.return_value = mock_presence_response
    
    # Execute
    result = get_active_users(include_presence=True)
    
    # Assert
    assert len(result) == 1
    assert result[0]["id"] == "U123456"
    assert result[0]["presence"] == "active"
    
    # Verify mocks
    mock_userlist.assert_called_once()
    mock_presence.assert_called_once_with(user_id="U123456")

@patch('api.slack.slack_service.userlist')
def test_get_active_users_with_longer_timeframe(mock_userlist, mock_userlist_response):
    # Setup
    mock_userlist.return_value = mock_userlist_response
    
    # Execute
    result = get_active_users(days=60)  # Longer timeframe should include testuser2
    
    # Assert
    assert len(result) == 2
    user_ids = [user["id"] for user in result]
    assert "U123456" in user_ids
    assert "U234567" in user_ids
    
    # Verify mocks
    mock_userlist.assert_called_once()

@patch('api.slack.slack_service.rate_limited_get_user_info')
@patch('api.slack.slack_service.presence')
def test_get_user_details(mock_presence, mock_get_user_info, mock_user_info_response, mock_presence_response):
    # Setup
    mock_get_user_info.return_value = mock_user_info_response
    mock_presence.return_value = mock_presence_response
    
    # Execute
    result = get_user_details("U123456")
    
    # Assert
    assert result["id"] == "U123456"
    assert result["name"] == "testuser"
    assert result["real_name"] == "Test User"
    assert result["email"] == "testuser@example.com"
    assert result["presence"] == "active"
    assert result["phone"] == "123-456-7890"
    
    # Verify mocks
    mock_get_user_info.assert_called_once_with("U123456")
    mock_presence.assert_called_once_with(user_id="U123456")

@patch('api.slack.slack_service.rate_limited_get_user_info')
def test_get_user_details_not_found(mock_get_user_info):
    # Setup
    mock_get_user_info.return_value = None
    
    # Execute
    result = get_user_details("INVALID")
    
    # Assert
    assert result is None
    
    # Verify mocks
    mock_get_user_info.assert_called_once_with("INVALID")

@patch('api.slack.slack_service.clear_pattern')
def test_clear_slack_cache(mock_clear_pattern):
    # Setup
    mock_clear_pattern.return_value = True
    
    # Execute
    result = clear_slack_cache()
    
    # Assert
    assert result["success"] is True
    assert result["message"] == "All Slack caches cleared successfully"
    
    # Verify mocks
    assert mock_clear_pattern.call_count == 2
    mock_clear_pattern.assert_any_call("slack:active_users:*")
    mock_clear_pattern.assert_any_call("slack:user_details:*")