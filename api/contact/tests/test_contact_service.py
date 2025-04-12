import pytest
from unittest.mock import patch, MagicMock
from api.contact.contact_service import verify_recaptcha, send_confirmation_email, send_slack_notification

class TestContactService:
    """Test cases for the contact service module."""
    
    @patch('api.contact.contact_service.requests.post')
    def test_verify_recaptcha_success(self, mock_post):
        """Test successful reCAPTCHA verification."""
        # Setup
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response
        
        # Execute
        result = verify_recaptcha("test-token")
        
        # Assert
        assert result is True
        mock_post.assert_called_once()
    
    @patch('api.contact.contact_service.requests.post')
    def test_verify_recaptcha_failure(self, mock_post):
        """Test failed reCAPTCHA verification."""
        # Setup
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False}
        mock_post.return_value = mock_response
        
        # Execute
        result = verify_recaptcha("invalid-token")
        
        # Assert
        assert result is False
        mock_post.assert_called_once()
    
    @patch('api.contact.contact_service.resend.Emails.send')
    @patch('api.contact.contact_service.os.environ.get')
    def test_send_confirmation_email_success(self, mock_env_get, mock_send):
        """Test successful email sending."""
        # Setup
        mock_env_get.return_value = "test-api-key"
        mock_send.return_value = {"id": "test-email-id"}
        
        # Execute
        result = send_confirmation_email("John", "Doe", "john@example.com")
        
        # Assert
        assert result is True
        mock_send.assert_called_once()
    
    @patch('api.contact.contact_service.send_slack')
    def test_send_slack_notification_success(self, mock_send_slack):
        """Test successful Slack notification."""
        # Setup
        contact_data = {
            "id": "test-id",
            "firstName": "John",
            "lastName": "Doe",
            "email": "john@example.com",
            "organization": "Test Org",
            "inquiryType": "hackathon",
            "message": "Test message",
            "receiveUpdates": True
        }
        
        # Execute
        result = send_slack_notification(contact_data)
        
        # Assert
        assert result is True
        mock_send_slack.assert_called_once()