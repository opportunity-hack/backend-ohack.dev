import pytest
from unittest.mock import patch, MagicMock
import datetime
from api.slack.slack_service import get_active_users, get_user_details, clear_slack_cache, sync_slack_users_to_firestore

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


# --- Tests for sync_slack_users_to_firestore ---

@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_creates_new_users(mock_userlist, mock_fetch, mock_save, mock_userlist_response):
    mock_userlist.return_value = mock_userlist_response
    mock_fetch.return_value = None  # No existing users
    mock_save.return_value = MagicMock()  # Successful save

    result = sync_slack_users_to_firestore(lookback_days=30)

    assert result["created"] == 1  # Only U123456 is within 30 days and not bot/deleted
    assert result["skipped_existing"] == 0
    mock_save.assert_called_once()
    call_kwargs = mock_save.call_args
    assert "testuser1@example.com" in str(call_kwargs)


@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_skips_existing_users(mock_userlist, mock_fetch, mock_save, mock_userlist_response):
    mock_userlist.return_value = mock_userlist_response
    mock_fetch.return_value = MagicMock()  # User already exists

    result = sync_slack_users_to_firestore(lookback_days=30)

    assert result["created"] == 0
    assert result["skipped_existing"] == 1
    mock_save.assert_not_called()


@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_skips_bots_and_deleted(mock_userlist, mock_fetch, mock_save, mock_userlist_response):
    mock_userlist.return_value = mock_userlist_response
    mock_fetch.return_value = None

    result = sync_slack_users_to_firestore(lookback_days=30)

    # Bot (B345678) and deleted user (U456789) should be filtered out
    # U234567 is outside 30-day window
    # Only U123456 qualifies
    assert result["filtered_by_lookback"] == 1


@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_skips_users_without_email(mock_userlist, mock_fetch, mock_save):
    mock_userlist.return_value = {
        "members": [
            {
                "id": "U111111",
                "name": "noemail_user",
                "real_name": "No Email",
                "profile": {"display_name": "noemail"},
                "updated": int(datetime.datetime.now().timestamp()),
                "is_bot": False,
                "deleted": False,
            }
        ]
    }
    mock_fetch.return_value = None

    result = sync_slack_users_to_firestore(lookback_days=30)

    assert result["skipped_no_email"] == 1
    assert result["created"] == 0
    mock_save.assert_not_called()


@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_handles_save_failure(mock_userlist, mock_fetch, mock_save):
    mock_userlist.return_value = {
        "members": [
            {
                "id": "U999999",
                "name": "failuser",
                "real_name": "Fail User",
                "profile": {
                    "display_name": "failuser",
                    "email": "fail@example.com",
                    "image_192": "https://example.com/img.jpg",
                },
                "updated": int(datetime.datetime.now().timestamp()),
                "is_bot": False,
                "deleted": False,
            }
        ]
    }
    mock_fetch.return_value = None
    mock_save.side_effect = Exception("DB write failed")

    result = sync_slack_users_to_firestore(lookback_days=30)

    assert result["created"] == 0
    assert len(result["errors"]) == 1
    assert "DB write failed" in result["errors"][0]["reason"]


@patch('api.slack.slack_service.save_user')
@patch('api.slack.slack_service.fetch_user_by_user_id')
@patch('api.slack.slack_service.userlist')
def test_sync_with_longer_lookback(mock_userlist, mock_fetch, mock_save, mock_userlist_response):
    mock_userlist.return_value = mock_userlist_response
    mock_fetch.return_value = None
    mock_save.return_value = MagicMock()

    result = sync_slack_users_to_firestore(lookback_days=60)

    # With 60-day lookback, both U123456 (5 days) and U234567 (35 days) qualify
    assert result["filtered_by_lookback"] == 2
    assert result["created"] == 2


@patch('api.slack.slack_service.userlist')
def test_sync_handles_empty_userlist(mock_userlist):
    mock_userlist.return_value = None

    result = sync_slack_users_to_firestore()

    assert result["total_slack_members"] == 0
    assert len(result["errors"]) == 1