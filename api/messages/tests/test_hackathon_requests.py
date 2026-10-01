"""
Test cases for hackathon request admin service functions.

Tests get_all_hackathon_requests and admin_update_hackathon_request
from hackathons_service.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from services.hackathons_service import (
    get_all_hackathon_requests,
    admin_update_hackathon_request,
    get_hackathon_request_by_id,
    create_hackathon,
    update_hackathon_request,
    _render_request_summary_html,
)


class TestGetAllHackathonRequests:
    """Test cases for listing all hackathon requests."""

    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service._get_db')
    def test_empty_collection_returns_empty_list(self, mock_db):
        """Test that an empty collection returns an empty requests list."""
        mock_collection = MagicMock()
        mock_collection.stream.return_value = []
        mock_db.return_value.collection.return_value = mock_collection

        result = get_all_hackathon_requests()

        assert result == {"requests": []}

    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack')
    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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

    @patch('services.hackathons_service.send_slack')
    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
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
        # The full form payload is passed through so the email can include it
        assert mock_email.call_args[1]["request_data"]["companyName"] == "Email Corp"

    @patch('services.hackathons_service.send_slack')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_skips_email_without_contact_info(self, mock_db, mock_slack_audit, mock_slack):
        """Test that no email is sent if contact info is missing."""
        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db.return_value.collection.return_value = mock_collection

        payload = {"companyName": "No Contact Corp"}

        with patch('services.hackathons_service.send_hackathon_request_email') as mock_email:
            create_hackathon(payload)
            mock_email.assert_not_called()


class TestRenderRequestSummaryHtml:
    """Test cases for the submission summary embedded in the confirmation email."""

    def test_renders_submitted_fields_with_labels(self):
        html = _render_request_summary_html({
            "companyName": "ASU Coding Club",
            "organizationType": "university",
            "eventFormat": "in-person",
            "participantType": ["students", "industry-professionals"],
            "budget": 15000,
            "responsibilities": {"venue": "requestor", "judges": "shared"},
        })
        assert "Your Submission" in html
        assert "ASU Coding Club" in html
        assert "University" in html
        assert "In Person" in html
        assert "Students, Industry Professionals" in html
        assert "$15,000" in html
        assert "Venue &amp; equipment: Your organization" in html
        assert "Judges: Shared" in html

    def test_escapes_html_in_user_values(self):
        html = _render_request_summary_html({
            "companyName": '<script>alert("x")</script>',
        })
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_skips_empty_fields_and_internal_keys(self):
        html = _render_request_summary_html({
            "companyName": "Acme",
            "contactPhone": "",
            "alternateDate": None,
            "nonprofitSource": [],
            "donationPercentage": 0,
            "status": "pending",
            "agreeToContact": True,
        })
        assert "Contact phone" not in html
        assert "Alternate call date" not in html
        assert "Donation percentage" not in html
        assert "pending" not in html
        assert "agree" not in html.lower()

    def test_empty_or_missing_data_renders_nothing(self):
        assert _render_request_summary_html(None) == ""
        assert _render_request_summary_html({}) == ""
        assert _render_request_summary_html("not-a-dict") == ""

    def test_custom_theme_and_dates_humanized(self):
        html = _render_request_summary_html({
            "hackathonTheme": "custom",
            "customTheme": "AI for accessibility",
            "expectedHackathonDate": "2027-02-20T00:00:00.000Z",
        })
        assert "Custom — AI for accessibility" in html
        assert "February" in html and "2027" in html


# The frontend form's initial `formData` keys — copied from
# frontend-ohack.dev/src/components/HackathonRequest/HackathonRequestForm.js
# (useState(initialData || {...})). Keep in lockstep: a key missing from the
# backend allowlist is silently dropped from requester edits.
FRONTEND_FORM_KEYS = [
    "companyName", "organizationType", "contactName", "contactEmail", "contactPhone",
    "employeeCount", "participantType", "hackathonTheme", "customTheme",
    "expectedHackathonDate", "preferredDate", "alternateDate", "location", "eventFormat",
    "hasNonprofitList", "nonprofitDetails", "hasWorkedWithNonprofitsBefore",
    "nonprofitSource", "preferredNonprofitLocation", "specificRegion",
    "responsibilities", "budget", "donationPercentage", "additionalInfo",
    "agreeToContact", "agreeToTimeline",
]


def _request_doc(mock_db, stored):
    mock_doc_ref = MagicMock()
    snapshot = MagicMock()
    snapshot.exists = stored is not None
    snapshot.to_dict.return_value = stored
    mock_doc_ref.get.return_value = snapshot
    mock_db.return_value.collection.return_value.document.return_value = mock_doc_ref
    return mock_doc_ref


class TestUpdateHackathonRequest:
    """Public requester edit (capability link) — must not be a raw doc.update(json)."""

    def test_form_keys_are_all_editable(self):
        from services.hackathons_service import HACKATHON_REQUEST_EDITABLE_FIELDS
        assert set(FRONTEND_FORM_KEYS) <= set(HACKATHON_REQUEST_EDITABLE_FIELDS)
        for staff_key in ("status", "adminNotes", "created", "id", "updated"):
            assert staff_key not in HACKATHON_REQUEST_EDITABLE_FIELDS

    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_body_filtered_and_email_goes_to_stored_contact(self, mock_db, mock_audit, mock_email):
        """Before: doc.update got the whole body (status/adminNotes writable by
        anyone with the link) and the email went to body.contactEmail."""
        ref = _request_doc(mock_db, {"companyName": "old", "contactName": "Owner", "contactEmail": "owner@x", "status": "pending"})

        result = update_hackathon_request("req-1", {
            "status": "approved", "adminNotes": "pwned", "id": "other", "created": "x",
            "companyName": "x",
        })

        ref.update.assert_called_once()
        written = ref.update.call_args[0][0]
        assert set(written) == {"companyName", "updated"}
        assert written["companyName"] == "x"
        datetime.fromisoformat(written["updated"])
        mock_email.assert_called_once()
        assert mock_email.call_args[0][0] == "Owner"
        assert mock_email.call_args[0][1] == "owner@x"
        assert result is not None

    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_contact_email_change_does_not_redirect_confirmation(self, mock_db, mock_audit, mock_email):
        ref = _request_doc(mock_db, {"contactName": "Owner", "contactEmail": "owner@x"})
        update_hackathon_request("req-1", {"contactEmail": "attacker@x", "companyName": "x"})
        assert mock_email.call_args[0][1] == "owner@x"

    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_missing_doc_returns_none_without_email(self, mock_db, mock_audit, mock_email):
        """Before: emailed body.contactEmail, then crashed on None.update/None dict."""
        ref = _request_doc(mock_db, None)
        assert update_hackathon_request("nope", {"contactName": "A", "contactEmail": "a@x"}) is None
        mock_email.assert_not_called()
        ref.update.assert_not_called()


class TestHackathonRequestPublicRoutes:
    @pytest.fixture
    def client(self, monkeypatch):
        import importlib, sys
        from flask import Flask
        from test.common.auth_stubs import passthrough_auth_module
        monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
        sys.modules.pop("api.messages.messages_views", None)
        views = importlib.import_module("api.messages.messages_views")
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        yield views, app.test_client()
        sys.modules.pop("api.messages.messages_views", None)

    def test_patch_missing_request_is_404(self, client, monkeypatch):
        """Before: the view returned the service's None verbatim -> 500."""
        views, c = client
        monkeypatch.setattr(views, "update_hackathon_request", lambda rid, body: None)
        resp = c.patch("/api/messages/create-hackathon/nope", json={"companyName": "x"})
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "not_found"}


class TestPublicGetAndCreate:
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_public_get_strips_admin_notes(self, mock_db, mock_audit):
        _request_doc(mock_db, {"companyName": "A", "adminNotes": "internal"})
        result = get_hackathon_request_by_id("req-1")
        assert result["companyName"] == "A"
        assert "adminNotes" not in result

    @patch('services.hackathons_service.send_slack')
    @patch('services.hackathons_service.send_hackathon_request_email')
    @patch('services.hackathons_service.send_slack_audit')
    @patch('services.hackathons_service._get_db')
    def test_create_uses_random_uuid4_ids(self, mock_db, mock_audit, mock_email, mock_slack):
        """uuid1 ids embed the host MAC + timestamp (guessable capability link)."""
        import uuid
        result = create_hackathon({"companyName": "A"})
        assert uuid.UUID(hex=result["id"]).version == 4
