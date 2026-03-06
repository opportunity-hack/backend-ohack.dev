"""
Test cases for hackathon request admin service functions.

Tests get_all_hackathon_requests and admin_update_hackathon_request
from messages_service.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from api.messages.messages_service import (
    get_all_hackathon_requests,
    admin_update_hackathon_request,
    get_hackathon_request_by_id,
    create_hackathon,
    update_hackathon_request,
)


class TestGetAllHackathonRequests:
    """Test cases for listing all hackathon requests."""

    @patch('api.messages.messages_service.get_db')
    def test_returns_all_requests(self, mock_db):
        """Test that all hackathon requests are returned with their IDs."""
        # Setup
        mock_doc1 = MagicMock()
        mock_doc1.id = "request-1"
        mock_doc1.to_dict.return_value = {
            "companyName": "Acme Corp",
            "contactName": "Alice",
            "status": "pending",
            "created": "2025-06-01T10:00:00",
        }

        mock_doc2 = MagicMock()
        mock_doc2.id = "request-2"
        mock_doc2.to_dict.return_value = {
            "companyName": "Beta Inc",
            "contactName": "Bob",
            "status": "approved",
            "created": "2025-07-01T10:00:00",
        }

        mock_collection = MagicMock()
        mock_collection.stream.return_value = [mock_doc1, mock_doc2]
        mock_db.return_value.collection.return_value = mock_collection

        # Execute
        result = get_all_hackathon_requests()

        # Assert
        assert "requests" in result
        assert len(result["requests"]) == 2
        mock_db.return_value.collection.assert_called_once_with('hackathon_requests')

    @patch('api.messages.messages_service.get_db')
    def test_requests_include_document_ids(self, mock_db):
        """Test that each request includes its Firestore document ID."""
        mock_doc = MagicMock()
        mock_doc.id = "abc-123"
        mock_doc.to_dict.return_value = {
            "companyName": "Test Co",
            "created": "2025-01-01T00:00:00",
        }

        mock_collection = MagicMock()
        mock_collection.stream.return_value = [mock_doc]
        mock_db.return_value.collection.return_value = mock_collection

        result = get_all_hackathon_requests()

        assert result["requests"][0]["id"] == "abc-123"
        assert result["requests"][0]["companyName"] == "Test Co"

    @patch('api.messages.messages_service.get_db')
    def test_requests_sorted_newest_first(self, mock_db):
        """Test that requests are sorted by created date descending."""
        mock_doc_old = MagicMock()
        mock_doc_old.id = "old"
        mock_doc_old.to_dict.return_value = {
            "companyName": "Old Co",
            "created": "2025-01-01T00:00:00",
        }

        mock_doc_new = MagicMock()
        mock_doc_new.id = "new"
        mock_doc_new.to_dict.return_value = {
            "companyName": "New Co",
            "created": "2025-12-01T00:00:00",
        }

        mock_collection = MagicMock()
        # Return in wrong order to verify sorting
        mock_collection.stream.return_value = [mock_doc_old, mock_doc_new]
        mock_db.return_value.collection.return_value = mock_collection

        result = get_all_hackathon_requests()

        assert result["requests"][0]["id"] == "new"
        assert result["requests"][1]["id"] == "old"

    @patch('api.messages.messages_service.get_db')
    def test_empty_collection_returns_empty_list(self, mock_db):
        """Test that an empty collection returns an empty requests list."""
        mock_collection = MagicMock()
        mock_collection.stream.return_value = []
        mock_db.return_value.collection.return_value = mock_collection

        result = get_all_hackathon_requests()

        assert result == {"requests": []}

    @patch('api.messages.messages_service.get_db')
    def test_handles_missing_created_field(self, mock_db):
        """Test that requests without a created field are still returned."""
        mock_doc = MagicMock()
        mock_doc.id = "no-date"
        mock_doc.to_dict.return_value = {
            "companyName": "No Date Co",
            "status": "pending",
        }

        mock_collection = MagicMock()
        mock_collection.stream.return_value = [mock_doc]
        mock_db.return_value.collection.return_value = mock_collection

        result = get_all_hackathon_requests()

        assert len(result["requests"]) == 1
        assert result["requests"][0]["companyName"] == "No Date Co"


class TestAdminUpdateHackathonRequest:
    """Test cases for admin updating a hackathon request."""

    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_updates_status_successfully(self, mock_db, mock_slack):
        """Test that an admin can update the status of a request."""
        # Setup
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_doc_ref.get.return_value = mock_snapshot

        # After update, return updated doc
        updated_dict = {
            "companyName": "Test Co",
            "status": "approved",
            "adminNotes": "Looks good",
            "updated": "2025-07-01T00:00:00",
        }
        # First get() for exists check, second get() after update
        mock_snapshot_after = MagicMock()
        mock_snapshot_after.to_dict.return_value = updated_dict
        mock_doc_ref.get.side_effect = [mock_snapshot, mock_snapshot_after]

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db.return_value.collection.return_value = mock_collection

        # Execute
        result = admin_update_hackathon_request("req-123", {
            "status": "approved",
            "adminNotes": "Looks good",
        })

        # Assert
        assert result is not None
        assert result["id"] == "req-123"
        mock_doc_ref.update.assert_called_once()
        update_args = mock_doc_ref.update.call_args[0][0]
        assert update_args["status"] == "approved"
        assert update_args["adminNotes"] == "Looks good"
        assert "updated" in update_args

    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_returns_none_for_nonexistent_request(self, mock_db, mock_slack):
        """Test that updating a nonexistent request returns None."""
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc_ref.get.return_value = mock_snapshot

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db.return_value.collection.return_value = mock_collection

        result = admin_update_hackathon_request("nonexistent-id", {
            "status": "approved",
        })

        assert result is None
        mock_doc_ref.update.assert_not_called()

    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_adds_updated_timestamp(self, mock_db, mock_slack):
        """Test that the updated timestamp is added to the update payload."""
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_doc_ref.get.return_value = mock_snapshot

        mock_snapshot_after = MagicMock()
        mock_snapshot_after.to_dict.return_value = {"status": "in-progress"}
        mock_doc_ref.get.side_effect = [mock_snapshot, mock_snapshot_after]

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db.return_value.collection.return_value = mock_collection

        admin_update_hackathon_request("req-456", {"status": "in-progress"})

        update_args = mock_doc_ref.update.call_args[0][0]
        assert "updated" in update_args
        # Verify it's a valid ISO format timestamp
        datetime.fromisoformat(update_args["updated"])

    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_sends_slack_audit(self, mock_db, mock_slack):
        """Test that updating a request sends a Slack audit message."""
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_doc_ref.get.return_value = mock_snapshot

        mock_snapshot_after = MagicMock()
        mock_snapshot_after.to_dict.return_value = {}
        mock_doc_ref.get.side_effect = [mock_snapshot, mock_snapshot_after]

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db.return_value.collection.return_value = mock_collection

        admin_update_hackathon_request("req-789", {"status": "rejected"})

        mock_slack.assert_called_once()
        call_kwargs = mock_slack.call_args[1]
        assert call_kwargs["action"] == "admin_update_hackathon_request"
        assert call_kwargs["message"] == "Admin updating"
        assert call_kwargs["payload"]["status"] == "rejected"


class TestGetHackathonRequestById:
    """Test cases for retrieving a single hackathon request."""

    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_returns_request_data(self, mock_db, mock_slack):
        """Test that a request is returned by its document ID."""
        mock_doc = MagicMock()
        mock_doc_data = MagicMock()
        mock_doc_data.to_dict.return_value = {
            "companyName": "Found Co",
            "status": "pending",
        }
        mock_doc.get.return_value = mock_doc_data

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db.return_value.collection.return_value = mock_collection

        result = get_hackathon_request_by_id("doc-123")

        assert result["companyName"] == "Found Co"
        mock_collection.document.assert_called_once_with("doc-123")


class TestCreateHackathon:
    """Test cases for creating a new hackathon request."""

    @patch('api.messages.messages_service.send_slack')
    @patch('api.messages.messages_service.send_hackathon_request_email')
    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_creates_request_with_pending_status(self, mock_db, mock_slack_audit, mock_email, mock_slack):
        """Test that a new request is created with pending status."""
        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db.return_value.collection.return_value = mock_collection

        payload = {
            "companyName": "New Corp",
            "contactName": "Charlie",
            "contactEmail": "charlie@example.com",
        }

        result = create_hackathon(payload)

        assert result["success"] is True
        assert result["message"] == "Hackathon Request Created"
        assert "id" in result
        # Verify the data was saved with pending status
        saved_data = mock_doc.set.call_args[0][0]
        assert saved_data["status"] == "pending"
        assert "created" in saved_data

    @patch('api.messages.messages_service.send_slack')
    @patch('api.messages.messages_service.send_hackathon_request_email')
    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_sends_confirmation_email(self, mock_db, mock_slack_audit, mock_email, mock_slack):
        """Test that a confirmation email is sent on creation."""
        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db.return_value.collection.return_value = mock_collection

        payload = {
            "companyName": "Email Corp",
            "contactName": "Diana",
            "contactEmail": "diana@example.com",
        }

        create_hackathon(payload)

        mock_email.assert_called_once()
        call_args = mock_email.call_args[0]
        assert call_args[0] == "Diana"
        assert call_args[1] == "diana@example.com"

    @patch('api.messages.messages_service.send_slack')
    @patch('api.messages.messages_service.send_slack_audit')
    @patch('api.messages.messages_service.get_db')
    def test_skips_email_without_contact_info(self, mock_db, mock_slack_audit, mock_slack):
        """Test that no email is sent if contact info is missing."""
        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db.return_value.collection.return_value = mock_collection

        payload = {"companyName": "No Contact Corp"}

        with patch('api.messages.messages_service.send_hackathon_request_email') as mock_email:
            create_hackathon(payload)
            mock_email.assert_not_called()
