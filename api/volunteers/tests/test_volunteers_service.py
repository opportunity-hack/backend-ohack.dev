import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from services.volunteers_service import (
    create_or_update_volunteer,
    get_volunteer_by_user_id,
    get_volunteers_by_event,
    generate_qr_code
)

# Mock data for tests
MOCK_USER_ID = "test-user-123"
MOCK_EMAIL = "test@example.com"
MOCK_EVENT_ID = "test-event-456"

MOCK_MENTOR_DATA = {
    "name": "Test Mentor",
    "volunteer_type": "mentor",
    "type": "mentors",
    "expertise": "programming,design",
    "company": "Test Company",
    "shortBio": "Short bio here"
}

MOCK_VOLUNTEER_DOC = {
    "id": "abc-123",
    "user_id": MOCK_USER_ID,
    "email": MOCK_EMAIL,
    "event_id": MOCK_EVENT_ID,
    "volunteer_type": "mentor",
    "type": "mentors",
    "name": "Test Mentor",
    "isSelected": False
}

@patch('services.volunteers_service.get_db')
def test_create_volunteer(mock_get_db):
    # Setup mock
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    
    # Mock get_volunteer_by_user_id to return None (new volunteer)
    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None):
        # Mock get_slack_user_by_email
        with patch('services.volunteers_service.get_slack_user_by_email', return_value={"id": "slack-123"}):
            # Call the function
            result = create_or_update_volunteer(
                user_id=MOCK_USER_ID,
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data=MOCK_MENTOR_DATA
            )
            
            # Assertions
            assert result is not None
            assert result["user_id"] == MOCK_USER_ID
            assert result["email"] == MOCK_EMAIL
            assert result["event_id"] == MOCK_EVENT_ID
            assert result["volunteer_type"] == "mentor"
            assert result["slack_user_id"] == "slack-123"
            assert "id" in result
            
            # Verify database calls
            mock_db.collection.assert_called_with('volunteers')
            mock_collection.document.assert_called_once()
            mock_doc.set.assert_called_once()

@patch('services.volunteers_service.get_db')
def test_update_volunteer(mock_get_db):
    # Setup mock
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    
    # Mock get_volunteer_by_user_id to return existing volunteer
    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=MOCK_VOLUNTEER_DOC):
        # Call the function
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID,
            email=MOCK_EMAIL,
            event_id=MOCK_EVENT_ID,
            volunteer_data={
                **MOCK_MENTOR_DATA,
                "shortBio": "Updated bio"
            }
        )
        
        # Assertions
        assert result is not None
        assert result["id"] == "abc-123"
        assert result["shortBio"] == "Updated bio"
        
        # Verify database calls
        mock_db.collection.assert_called_with('volunteers')
        mock_collection.document.assert_called_once_with("abc-123")
        mock_doc.update.assert_called_once()

@patch('services.volunteers_service.get_db')
def test_get_volunteers_by_event(mock_get_db):
    # Setup mock
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    
    mock_collection = MagicMock()
    mock_where1 = MagicMock()
    mock_where2 = MagicMock()
    mock_limit = MagicMock()
    mock_offset = MagicMock()
    
    mock_db.collection.return_value = mock_collection
    mock_collection.where.return_value = mock_where1
    mock_where1.where.return_value = mock_where2
    mock_where2.limit.return_value = mock_limit
    mock_limit.offset.return_value = mock_offset
    
    # Mock return value
    mock_volunteer1 = MagicMock()
    mock_volunteer1.to_dict.return_value = {
        "id": "abc-123",
        "name": "Mentor 1",
        "volunteer_type": "mentor"
    }
    
    mock_volunteer2 = MagicMock()
    mock_volunteer2.to_dict.return_value = {
        "id": "def-456",
        "name": "Mentor 2",
        "volunteer_type": "mentor"
    }
    
    mock_offset.stream.return_value = [mock_volunteer1, mock_volunteer2]
    
    # Call the function
    result = get_volunteers_by_event(
        event_id=MOCK_EVENT_ID,
        volunteer_type="mentor",
        page=1,
        limit=10
    )
    
    # Assertions
    assert len(result) == 2
    assert result[0]["id"] == "abc-123"
    assert result[1]["id"] == "def-456"
    
    # Verify database calls
    mock_db.collection.assert_called_with('volunteers')
    mock_collection.where.assert_called_with('event_id', '==', MOCK_EVENT_ID)
    mock_where1.where.assert_called_with('volunteer_type', '==', 'mentor')
    mock_where2.limit.assert_called_with(10)
    mock_limit.offset.assert_called_with(0)


def test_generate_qr_code():
    """Test QR code generation functionality."""
    test_content = "https://www.ohack.dev/test-link"
    
    # Generate QR code
    qr_image_bytes = generate_qr_code(test_content)
    
    # Assertions
    assert qr_image_bytes is not None
    assert isinstance(qr_image_bytes, bytes)
    assert len(qr_image_bytes) > 0
    
    # Verify it's a valid PNG by checking the PNG header
    png_header = b'\x89PNG\r\n\x1a\n'
    assert qr_image_bytes.startswith(png_header)


def test_generate_qr_code_empty_content():
    """Test QR code generation with empty content."""
    # Generate QR code with empty string
    qr_image_bytes = generate_qr_code("")
    
    # Should still generate a valid QR code
    assert qr_image_bytes is not None
    assert isinstance(qr_image_bytes, bytes)
    assert len(qr_image_bytes) > 0


def test_generate_qr_code_special_characters():
    """Test QR code generation with special characters."""
    test_content = "Hello! 🌟 Special chars: @#$%^&*()_+{}|:<>?[];',./"
    
    # Generate QR code
    qr_image_bytes = generate_qr_code(test_content)
    
    # Assertions
    assert qr_image_bytes is not None
    assert isinstance(qr_image_bytes, bytes)
    assert len(qr_image_bytes) > 0