import logging

import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from services.volunteers_service import (
    create_or_update_volunteer,
    get_calendar_email_attachment_from_availability,
    get_volunteer_by_user_id,
    get_volunteers_by_event,
    get_user_hackathon_attendance,
    get_my_mentor_events,
    _did_volunteer_attend,
    generate_qr_code,
    update_volunteer_selection,
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
    
    # Mock get_volunteer_by_user_id to return None (new volunteer). The shared
    # identity resolver then falls back to PropelAuth — stub that out too so
    # tests never make a network call.
    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None), \
         patch('services.users_service.get_propel_user_details_by_id', return_value=None):
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
        
        # Verify database calls. The update path writes via set(..., merge=True)
        # so that brand-new fields land too.
        mock_db.collection.assert_called_with('volunteers')
        mock_collection.document.assert_called_once_with("abc-123")
        mock_doc.set.assert_called_once()


# ---------------------------------------------------------------------------
# Staff-owned fields (STAFF_OWNED_VOLUNTEER_FIELDS)
#
# An applicant's own submit/update must never write approval, check-in or
# refund state. Regression cover for the bug where every application edit sent
# `isSelected: false` from the form's initial state and silently un-approved
# an already-approved mentor/judge/volunteer/sponsor.
# ---------------------------------------------------------------------------

# The notification fan-out at the end of create/update talks to Slack and
# Resend for real; these tests only care about what gets written to Firestore.
def _silence_notifications():
    return [
        patch('services.volunteers_service.send_admin_notification_email'),
        patch('services.volunteers_service.send_slack_volunteer_notification'),
        patch('services.volunteers_service.send_volunteer_confirmation_email'),
        patch('services.volunteers_service.get_slack_user_by_email', return_value=None),
    ]


def _written_doc(mock_doc):
    """The dict handed to Firestore by whichever write path ran."""
    if mock_doc.set.called:
        return mock_doc.set.call_args[0][0]
    return mock_doc.update.call_args[0][0]


@patch('services.volunteers_service.get_db')
def test_update_does_not_let_applicant_reset_isSelected(mock_get_db):
    """An approved volunteer editing their application stays approved."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc

    approved = {**MOCK_VOLUNTEER_DOC, "isSelected": True}

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=approved):
        for p in _silence_notifications():
            p.start()
        try:
            result = create_or_update_volunteer(
                user_id=MOCK_USER_ID,
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data={
                    **MOCK_MENTOR_DATA,
                    "shortBio": "Updated bio",
                    # What the mentor/volunteer/sponsor forms actually send.
                    "isSelected": False,
                },
            )
        finally:
            patch.stopall()

    written = _written_doc(mock_doc)
    assert written["isSelected"] is True, "applicant payload must not un-approve"
    assert result["isSelected"] is True
    # The rest of the edit still goes through.
    assert written["shortBio"] == "Updated bio"


@patch('services.volunteers_service.get_db')
def test_update_does_not_let_applicant_clobber_checkin_or_refund_state(mock_get_db):
    """Check-in and refund bookkeeping are staff/system-owned."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc

    existing = {
        **MOCK_VOLUNTEER_DOC,
        "isSelected": True,
        "checkInTime": "2026-10-12T09:00:00",
        "isCheckedIn": True,
        "deposit_status": "refunded",
    }

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=existing):
        for p in _silence_notifications():
            p.start()
        try:
            create_or_update_volunteer(
                user_id=MOCK_USER_ID,
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data={
                    **MOCK_MENTOR_DATA,
                    "checkInTime": None,
                    "isCheckedIn": False,
                    "deposit_status": "paid",
                    "deposit_refund_id": "re_attacker",
                },
            )
        finally:
            patch.stopall()

    written = _written_doc(mock_doc)
    # Omitted from the merge write, so Firestore keeps the stored values.
    for field in ("checkInTime", "isCheckedIn", "deposit_status", "deposit_refund_id"):
        assert field not in written, f"{field} must not be writable by an applicant"


@patch('services.volunteers_service.get_db')
def test_create_ignores_applicant_supplied_isSelected(mock_get_db):
    """A first-time applicant cannot self-approve via the payload."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None), \
         patch('services.users_service.get_propel_user_details_by_id', return_value=None):
        for p in _silence_notifications():
            p.start()
        try:
            result = create_or_update_volunteer(
                user_id=MOCK_USER_ID,
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data={**MOCK_MENTOR_DATA, "isSelected": True},
            )
        finally:
            patch.stopall()

    assert _written_doc(mock_doc)["isSelected"] is False
    assert result["isSelected"] is False


@patch('services.volunteers_service.get_db')
def test_update_still_accepts_deposit_payment_fields(mock_get_db):
    """The hacker Stripe return sets these on /update — must not be stripped."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc

    existing = {**MOCK_VOLUNTEER_DOC, "volunteer_type": "hacker", "type": "hackers"}

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=existing):
        for p in _silence_notifications():
            p.start()
        try:
            create_or_update_volunteer(
                user_id=MOCK_USER_ID,
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data={
                    "volunteer_type": "hacker",
                    "type": "hackers",
                    "stripe_payment_intent_id": "pi_123",
                    "deposit_amount_cents": 2500,
                    "deposit_disposition": "refund",
                },
            )
        finally:
            patch.stopall()

    written = _written_doc(mock_doc)
    assert written["stripe_payment_intent_id"] == "pi_123"
    assert written["deposit_amount_cents"] == 2500
    assert written["deposit_disposition"] == "refund"


@patch('services.volunteers_service.get_db')
def test_update_resolves_existing_doc_by_email_when_user_id_differs(mock_get_db):
    """The duplicate-doc bug: a volunteer doc stored under a different identity
    shape (email match, OAuth user_id, admin-created) must hit the UPDATE
    branch, not spawn a second isSelected=False doc. The write path uses the
    same 3-way resolver as the GET route (find_volunteer_by_caller_identity)."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc

    # Doc was stored with an OAuth-shaped user_id, so the propel-UUID lookup
    # misses; the PropelAuth-email lookup is what finds it.
    approved_legacy_doc = {
        **MOCK_VOLUNTEER_DOC,
        "user_id": "oauth2|slack|T1Q7936BH-U123ABC",
        "isSelected": True,
    }

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None), \
         patch('services.users_service.get_propel_user_details_by_id',
               return_value=(MOCK_EMAIL, "oauth2|slack|T1Q7936BH-U123ABC", None, None, "Test Mentor", "tm")), \
         patch('services.volunteers_service.get_volunteer_by_email', return_value=approved_legacy_doc):
        for p in _silence_notifications():
            p.start()
        try:
            result = create_or_update_volunteer(
                user_id=MOCK_USER_ID,  # propel UUID != stored user_id
                email=MOCK_EMAIL,
                event_id=MOCK_EVENT_ID,
                volunteer_data={**MOCK_MENTOR_DATA, "shortBio": "Edited bio"},
            )
        finally:
            patch.stopall()

    # UPDATE branch: writes to the EXISTING doc id — no new doc created.
    mock_collection.document.assert_called_once_with("abc-123")
    written = _written_doc(mock_doc)
    assert written["shortBio"] == "Edited bio"
    # Approval and the doc's original identity survive the edit.
    assert written["isSelected"] is True
    assert written["user_id"] == "oauth2|slack|T1Q7936BH-U123ABC"
    assert result["isSelected"] is True


# ---------------------------------------------------------------------------
# update_volunteer_selection — the single admin writer for isSelected
# (POST /api/admin/volunteer/<id>/select).
# ---------------------------------------------------------------------------

def _selection_db(mock_get_db, existing):
    """Wire get_db() so volunteers/<id>.get().to_dict() returns `existing`."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    mock_doc.get.return_value.to_dict.return_value = dict(existing)
    return mock_collection, mock_doc


@patch('services.volunteers_service.get_db')
def test_update_volunteer_selection_clears_event_cache(mock_get_db):
    """
    The admin roster list is served from hackathons_service.get_volunteer_by_event,
    an in-process TTLCache. A toggle must bust it or the list keeps showing the
    pre-toggle flag until the TTL lapses.
    """
    _selection_db(mock_get_db, MOCK_VOLUNTEER_DOC)

    with patch('services.hackathons_service.get_volunteer_by_event') as mock_by_event:
        update_volunteer_selection("abc-123", True, "admin-x")

    mock_by_event.cache_clear.assert_called_once()


@patch('services.volunteers_service.get_db')
def test_update_volunteer_selection_sets_flag_and_audit_fields(mock_get_db):
    """Writes exactly isSelected + who/when — nothing else on the doc moves."""
    mock_collection, mock_doc = _selection_db(mock_get_db, MOCK_VOLUNTEER_DOC)

    with patch('services.hackathons_service.get_volunteer_by_event'):
        result = update_volunteer_selection("abc-123", True, "admin-x")

    mock_collection.document.assert_called_once_with("abc-123")
    written = mock_doc.update.call_args[0][0]
    assert written["isSelected"] is True
    assert written["updated_by"] == "admin-x"
    assert written["updated_timestamp"]
    assert set(written) == {"isSelected", "updated_by", "updated_timestamp"}

    # Returned record reflects the write on top of the stored doc.
    assert result["isSelected"] is True
    assert result["updated_by"] == "admin-x"
    assert result["event_id"] == MOCK_EVENT_ID


@patch('services.volunteers_service.get_db')
def test_update_volunteer_selection_missing_doc_returns_none(mock_get_db):
    """The view maps None → 404; nothing must be written for an unknown id."""
    _, mock_doc = _selection_db(mock_get_db, {})
    mock_doc.get.return_value.to_dict.return_value = None

    assert update_volunteer_selection("nope", True, "admin-x") is None
    mock_doc.update.assert_not_called()


@patch('services.volunteers_service.get_db')
def test_update_volunteer_selection_sends_slack_audit_when_enabled(mock_get_db):
    """Parity with the hackathon PATCH route: every toggle is Slack-audited
    (outside ENVIRONMENT=test, where the notification gate suppresses it)."""
    _selection_db(mock_get_db, MOCK_VOLUNTEER_DOC)

    with patch('services.volunteers_service._notifications_disabled', return_value=False), \
         patch('services.hackathons_service.get_volunteer_by_event'), \
         patch('common.utils.slack.send_slack_audit') as mock_audit:
        update_volunteer_selection("abc-123", False, "admin-x")

    mock_audit.assert_called_once()
    kwargs = mock_audit.call_args.kwargs
    assert kwargs["action"] == "update_volunteer_selection"
    assert kwargs["payload"] == {
        "volunteer_id": "abc-123",
        "event_id": MOCK_EVENT_ID,
        "volunteer_type": "mentor",
        "isSelected": False,
        "updated_by": "admin-x",
    }


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


def _make_snap(doc_id, data):
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    return snap


def test_did_volunteer_attend_filter_rules():
    # Hackers always count
    assert _did_volunteer_attend({"volunteer_type": "hacker"}) is True
    # Mentors / judges / volunteers need both isSelected and a check-in
    assert _did_volunteer_attend({"volunteer_type": "mentor", "isSelected": True, "checkInTime": "2024-10-12T09:00:00"}) is True
    assert _did_volunteer_attend({"volunteer_type": "mentor", "isSelected": True}) is False
    assert _did_volunteer_attend({"volunteer_type": "mentor", "checkInTime": "2024-10-12T09:00:00"}) is False
    assert _did_volunteer_attend({"volunteer_type": "judge", "isSelected": False, "checkInTime": "x"}) is False
    assert _did_volunteer_attend({"volunteer_type": "volunteer", "isSelected": True, "checkInTime": "x"}) is True
    # Sponsor / unknown roles don't appear in attendance history
    assert _did_volunteer_attend({"volunteer_type": "sponsor", "isSelected": True, "checkInTime": "x"}) is False
    assert _did_volunteer_attend({"volunteer_type": ""}) is False


@patch('services.volunteers_service.get_db')
def test_get_user_hackathon_attendance_filters_and_groups(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    user_id = "U_TEST"

    # Volunteer collection records — same user across two events with mixed roles
    volunteer_records = [
        # Event-A: hacker (always counts)
        _make_snap("v1", {
            "id": "v1", "user_id": user_id, "event_id": "EVT_A",
            "volunteer_type": "hacker",
        }),
        # Event-A: mentor selected + checked in -> counts; collapses with hacker
        _make_snap("v2", {
            "id": "v2", "user_id": user_id, "event_id": "EVT_A",
            "volunteer_type": "mentor", "isSelected": True,
            "checkInTime": "2024-10-12T09:00:00",
        }),
        # Event-B: judge selected but never checked in -> excluded
        _make_snap("v3", {
            "id": "v3", "user_id": user_id, "event_id": "EVT_B",
            "volunteer_type": "judge", "isSelected": True,
        }),
        # Event-C: volunteer not selected -> excluded
        _make_snap("v4", {
            "id": "v4", "user_id": user_id, "event_id": "EVT_C",
            "volunteer_type": "volunteer", "isSelected": False,
            "checkInTime": "2024-10-12T09:00:00",
        }),
        # Event-D: sponsor (always excluded from history)
        _make_snap("v5", {
            "id": "v5", "user_id": user_id, "event_id": "EVT_D",
            "volunteer_type": "sponsor", "isSelected": True,
            "checkInTime": "2024-10-12T09:00:00",
        }),
    ]

    # Hackathon collection record for the one event we expect to surface (Event-A)
    hackathon_snap = _make_snap("h1", {
        "event_id": "EVT_A",
        "title": "Fall 2024 Hack",
        "start_date": "2024-10-12",
        "end_date": "2024-10-13",
        "location": "ASU Tempe",
        "image_url": "https://cdn/img.png",
        "links": [],
        "nonprofits": [],  # avoid touching db.get_all
    })

    # Configure db.collection() to return different query mocks per call
    volunteer_query = MagicMock()
    volunteer_query.stream.return_value = volunteer_records
    # Email fallback — return nothing additional
    empty_query = MagicMock()
    empty_query.stream.return_value = []

    volunteer_collection = MagicMock()
    # First .where('user_id', ...) returns volunteer_query; .where('email', ...) returns empty
    def volunteer_where(field, op, value):
        if field == 'user_id':
            return volunteer_query
        return empty_query
    volunteer_collection.where.side_effect = volunteer_where

    hackathon_evt_a_query = MagicMock()
    hackathon_evt_a_query.stream.return_value = [hackathon_snap]
    hackathon_evt_a_query.limit.return_value = hackathon_evt_a_query

    hackathon_collection = MagicMock()
    hackathon_where = MagicMock()
    hackathon_where.limit.return_value = hackathon_evt_a_query
    hackathon_collection.where.return_value = hackathon_where

    def collection_router(name):
        if name == 'volunteers':
            return volunteer_collection
        if name == 'hackathons':
            return hackathon_collection
        return MagicMock()

    mock_db.collection.side_effect = collection_router

    result = get_user_hackathon_attendance(user_id=user_id)

    assert len(result) == 1, f"Only EVT_A should pass attendance filter; got {result}"
    entry = result[0]
    assert entry['event_id'] == 'EVT_A'
    assert entry['title'] == 'Fall 2024 Hack'
    # Roles collapse onto a single card
    assert sorted(entry['roles']) == ['Hacker', 'Mentor']


# ---------------------------------------------------------------------------
# Calendar attachments from availability
#
# Mentor/volunteer forms send machine-generated slot strings; the judge form's
# availability is free text. Free text must be skipped quietly — it used to
# fire an ERROR ("All patterns failed to match slot") into Sentry on every
# judge submission that filled the field.
# ---------------------------------------------------------------------------

def test_calendar_attachments_skip_free_text_availability(caplog):
    free_text = (
        "I will be Present near ASU during Entire November month for Client "
        "visit and will be available all month"
    )
    with caplog.at_level(logging.DEBUG):
        result = get_calendar_email_attachment_from_availability(
            free_text, "judge@example.com", volunteer_type="judge"
        )

    assert result == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"free-text availability must not log errors: {errors}"


def test_calendar_attachments_parse_structured_availability():
    structured = (
        "Saturday, Oct 11: ☀️ Morning (9am - 12pm PST), "
        "Sunday, Oct 12: 🏙️ Afternoon (1pm - 3pm PST)"
    )
    result = get_calendar_email_attachment_from_availability(
        structured, "mentor@example.com", volunteer_type="mentor", year=2026
    )

    assert len(result) == 2
    for attachment in result:
        assert attachment["type"] == "text/calendar"
        assert attachment["filename"].endswith(".ics")
        assert "BEGIN:VCALENDAR" in attachment["content"]
        assert "ATTENDEE:MAILTO:mentor@example.com" in attachment["content"]


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

# ---------------------------------------------------------------------------
# get_volunteer_self_status — generic self-check (type=hacker etc.)
# ---------------------------------------------------------------------------
from services.volunteers_service import get_volunteer_self_status  # noqa: E402


@patch('services.volunteers_service.find_volunteer_by_caller_identity')
def test_get_volunteer_self_status_selected_hacker(mock_find):
    mock_find.return_value = {"name": "Jamie Hacker", "isSelected": True}
    result = get_volunteer_self_status(MOCK_USER_ID, MOCK_EVENT_ID, "hacker")
    assert result == {"is_hacker": True, "volunteer": {"name": "Jamie Hacker", "isSelected": True}}
    mock_find.assert_called_once_with(MOCK_USER_ID, MOCK_EVENT_ID, "hacker")


@patch('services.volunteers_service.find_volunteer_by_caller_identity')
def test_get_volunteer_self_status_not_selected_returns_null_volunteer(mock_find):
    mock_find.return_value = {"name": "Jamie Hacker", "isSelected": False}
    result = get_volunteer_self_status(MOCK_USER_ID, MOCK_EVENT_ID, "hacker")
    assert result == {"is_hacker": False, "volunteer": None}


@patch('services.volunteers_service.find_volunteer_by_caller_identity')
def test_get_volunteer_self_status_no_doc_returns_false(mock_find):
    mock_find.return_value = None
    result = get_volunteer_self_status(MOCK_USER_ID, MOCK_EVENT_ID, "hacker")
    assert result == {"is_hacker": False, "volunteer": None}


@patch('services.volunteers_service.find_volunteer_by_caller_identity')
def test_get_volunteer_self_status_leaks_no_pii_beyond_name(mock_find):
    mock_find.return_value = {"name": "Jamie", "isSelected": True, "email": "jamie@example.com", "phone": "555-1234"}
    result = get_volunteer_self_status(MOCK_USER_ID, MOCK_EVENT_ID, "hacker")
    assert set(result["volunteer"].keys()) == {"name", "isSelected"}


# ---------------------------------------------------------------------------
# /mentor dashboard: every mentor application across events, no attendance filter


def _mentor_events_snap(doc_id, data):
    snap = MagicMock()
    snap.id = doc_id
    snap.exists = True
    snap.to_dict.return_value = data
    return snap


def _query_returning(items):
    q = MagicMock()
    q.stream.return_value = items
    q.limit.return_value = q
    return q


@patch('services.volunteers_service.get_db')
def test_get_my_mentor_events_lists_every_mentor_application(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    propel_id = "propel-uuid-1"
    email = "mentor@example.com"
    oauth_id = "oauth2|slack|T1-U1"

    by_propel = [
        # Approved + checked in -> returned with both booleans true
        _mentor_events_snap("v1", {
            "user_id": propel_id, "event_id": "EVT_A", "volunteer_type": "mentor",
            "name": "Me", "email": email, "isSelected": True, "isCheckedIn": True,
            "checkInTime": "2026-10-11T09:00:00", "availability": "Sat Oct 11: Morning",
            "inPerson": "Yes!", "shortBio": "long-form answer that must not be returned",
        }),
        # Pending (no isSelected) -> still returned (the dashboard shows it as awaiting)
        _mentor_events_snap("v2", {
            "user_id": propel_id, "event_id": "EVT_B", "volunteer_type": "mentor",
            "status": "pending",
        }),
        # Hacker application for the same event -> ignored
        _mentor_events_snap("v3", {
            "user_id": propel_id, "event_id": "EVT_A", "volunteer_type": "hacker",
        }),
        # Mentor doc for an event with no hackathon record -> dropped
        _mentor_events_snap("v5", {
            "user_id": propel_id, "event_id": "EVT_MISSING", "volunteer_type": "mentor",
        }),
    ]
    # Doc keyed on the email only (admin import) -> found through the email query
    by_email = [
        _mentor_events_snap("v4", {
            "email": email, "event_id": "EVT_C", "volunteer_type": "mentor", "isSelected": True,
        }),
        # Same doc id surfaced twice must dedupe
        by_propel[0],
    ]

    volunteer_collection = MagicMock()

    def volunteer_where(field, op, value):
        if field == 'user_id' and value == propel_id:
            return _query_returning(by_propel)
        if field == 'email' and value == email:
            return _query_returning(by_email)
        return _query_returning([])
    volunteer_collection.where.side_effect = volunteer_where

    hackathons = {
        "EVT_A": {"event_id": "EVT_A", "title": "Fall 2026", "start_date": "2026-10-11",
                  "end_date": "2026-10-12", "location": "Tempe", "timezone": "America/Phoenix",
                  "teams": ["heavy"], "nonprofits": ["heavy"]},
        "EVT_B": {"event_id": "EVT_B", "title": "Spring 2027", "start_date": "2027-03-01",
                  "end_date": "2027-03-02"},
        "EVT_C": {"event_id": "EVT_C", "title": "Summer 2025", "start_date": "2025-07-01",
                  "end_date": "2025-07-02"},
    }
    hackathon_collection = MagicMock()
    hackathon_collection.where.side_effect = lambda field, op, value: _query_returning(
        [_mentor_events_snap(f"h-{value}", hackathons[value])] if value in hackathons else []
    )

    mock_db.collection.side_effect = lambda name: {
        'volunteers': volunteer_collection,
        'hackathons': hackathon_collection,
    }.get(name, MagicMock())

    with patch('services.users_service.get_propel_user_details_by_id',
               return_value=(email, oauth_id, None, None, None, None)):
        result = get_my_mentor_events(propel_id)

    # Newest event first; EVT_MISSING dropped; hacker doc ignored; email doc found
    assert [r['event_id'] for r in result] == ['EVT_B', 'EVT_A', 'EVT_C']

    evt_a = result[1]
    assert evt_a['title'] == 'Fall 2026'
    assert evt_a['id'] == 'h-EVT_A'
    assert 'teams' not in evt_a and 'nonprofits' not in evt_a
    assert evt_a['application']['isSelected'] is True
    assert evt_a['application']['isCheckedIn'] is True
    assert evt_a['application']['availability'] == 'Sat Oct 11: Morning'
    assert evt_a['application']['inPerson'] == 'Yes!'
    # Lean: long-form answers and identity fields stay behind the per-event route
    for leaked in ('shortBio', 'name', 'email', 'user_id'):
        assert leaked not in evt_a['application']

    evt_b = result[0]
    assert evt_b['application']['isSelected'] is False
    assert evt_b['application']['isCheckedIn'] is False
    assert evt_b['application']['status'] == 'pending'


@patch('services.volunteers_service.get_db')
def test_get_my_mentor_events_prefers_the_approved_doc_per_event(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    propel_id = "propel-uuid-2"

    docs = [
        _mentor_events_snap("old", {"user_id": propel_id, "event_id": "EVT_A",
                                    "volunteer_type": "mentor", "timestamp": "2026-09-01T00:00:00"}),
        _mentor_events_snap("approved", {"user_id": propel_id, "event_id": "EVT_A",
                                         "volunteer_type": "mentor", "isSelected": True,
                                         "timestamp": "2026-08-01T00:00:00"}),
    ]
    volunteer_collection = MagicMock()
    volunteer_collection.where.side_effect = lambda field, op, value: _query_returning(
        docs if (field == 'user_id' and value == propel_id) else []
    )
    hackathon_collection = MagicMock()
    hackathon_collection.where.side_effect = lambda field, op, value: _query_returning(
        [_mentor_events_snap("h", {"event_id": "EVT_A", "title": "Fall", "start_date": "2026-10-11"})]
    )
    mock_db.collection.side_effect = lambda name: {
        'volunteers': volunteer_collection, 'hackathons': hackathon_collection,
    }.get(name, MagicMock())

    with patch('services.users_service.get_propel_user_details_by_id', return_value=None):
        result = get_my_mentor_events(propel_id)

    assert len(result) == 1
    assert result[0]['application']['isSelected'] is True


def test_get_my_mentor_events_without_identity_returns_empty():
    assert get_my_mentor_events("") == []
    assert get_my_mentor_events(None) == []



# ---------------------------------------------------------------------------
# Share cards: GET /api/volunteer/<event_id>/my-roles


class _FakeVolunteersQuery:
    """Minimal Firestore query double: equality filters over snapshot dicts."""

    def __init__(self, snaps, filters=()):
        self._snaps = snaps
        self._filters = filters

    def where(self, field, _op, value):
        return _FakeVolunteersQuery(self._snaps, self._filters + ((field, value),))

    def limit(self, _n):
        return self

    def stream(self):
        for snap in self._snaps:
            data = snap.to_dict()
            if all(data.get(f) == v for f, v in self._filters):
                yield snap


from services.volunteers_service import get_my_roles_for_event  # noqa: E402


@patch('services.volunteers_service._caller_identities')
@patch('services.volunteers_service.get_db')
def test_get_my_roles_for_event_returns_each_role_with_doc_id(mock_get_db, mock_ids):
    mock_ids.return_value = ("propel-1", "me@example.com", "oauth2|slack|T1-U1")
    snaps = [
        _mentor_events_snap("v-judge", {
            "user_id": "propel-1", "event_id": "EVT", "volunteer_type": "judge",
            "name": "Satish", "isSelected": True, "email": "me@example.com", "phone": "555",
        }),
        # Found only by verified email (admin-imported doc)
        _mentor_events_snap("v-mentor", {
            "user_id": "something-else", "email": "me@example.com", "event_id": "EVT",
            "volunteer_type": "mentor", "name": "Satish", "isSelected": False,
        }),
        # Found only by the legacy OAuth user_id
        _mentor_events_snap("v-hacker", {
            "user_id": "oauth2|slack|T1-U1", "event_id": "EVT", "volunteer_type": "hacker",
            "name": "Satish", "isSelected": True,
        }),
        # Other event -> ignored
        _mentor_events_snap("v-other", {
            "user_id": "propel-1", "event_id": "OTHER", "volunteer_type": "volunteer", "isSelected": True,
        }),
    ]
    mock_db = MagicMock()
    mock_db.collection.return_value = _FakeVolunteersQuery(snaps)
    mock_get_db.return_value = mock_db

    result = get_my_roles_for_event("propel-1", "EVT")

    assert result == {"roles": [
        {"type": "mentor", "id": "v-mentor", "isSelected": False, "name": "Satish"},
        {"type": "judge", "id": "v-judge", "isSelected": True, "name": "Satish"},
        {"type": "hacker", "id": "v-hacker", "isSelected": True, "name": "Satish"},
    ]}
    for role in result["roles"]:
        assert set(role.keys()) == {"type", "id", "isSelected", "name"}


@patch('services.volunteers_service._caller_identities')
@patch('services.volunteers_service.get_db')
def test_get_my_roles_for_event_empty_when_nothing_matches(mock_get_db, mock_ids):
    mock_ids.return_value = ("propel-1", None, None)
    mock_db = MagicMock()
    mock_db.collection.return_value = _FakeVolunteersQuery([])
    mock_get_db.return_value = mock_db
    assert get_my_roles_for_event("propel-1", "EVT") == {"roles": []}
    assert get_my_roles_for_event("", "EVT") == {"roles": []}


# ---------------------------------------------------------------------------
# Confirmation email — role-specific "what happens next" blocks
# ---------------------------------------------------------------------------

def _send_confirmation_capturing_html(volunteer_type, shifts=None, event=None):
    """Run send_volunteer_confirmation_email with Resend stubbed and return the html."""
    import os
    from services import volunteers_service
    sent = {}

    def fake_send(params):
        sent.update(params)
        return {"id": "re_test"}

    with patch("services.volunteers_service._notifications_disabled", return_value=False), \
         patch.dict(os.environ, {"RESEND_WELCOME_EMAIL_KEY": "re_dummy"}), \
         patch("services.volunteers_service.resend.Emails.send", side_effect=fake_send):
        result = volunteers_service.send_volunteer_confirmation_email(
            "Sam", "Doe", "sam@example.com", volunteer_type, None, "fall-2026", volunteer_id=None,
            shifts=shifts, event=event,
        )
    assert result == "re_test"
    return sent


def test_confirmation_email_volunteer_links_to_the_volunteer_guide():
    sent = _send_confirmation_capturing_html("volunteer")
    assert "https://www.ohack.dev/about/volunteers" in sent["html"]
    assert "check-in QR code" in sent["html"]
    # Event volunteers aren't staff-reviewed: no pending-review framing.
    assert "[Pending Review]" not in sent["subject"]
    assert "pending review" not in sent["html"].lower()


def test_confirmation_email_mentor_keeps_mentor_block_and_pending_banner():
    sent = _send_confirmation_capturing_html("mentor")
    assert "https://www.ohack.dev/about/mentors" in sent["html"]
    assert "https://www.ohack.dev/about/volunteers" not in sent["html"]
    assert sent["subject"].startswith("[Pending Review]")


VOLUNTEER_AVAILABILITY = (
    "Saturday, Nov 14: Doors Open & Registration - 📋 Check-in Registration (8:00am - 11:00am MST), "
    "Saturday, Nov 14: Afternoon Support - 🧹 Cleanup Crew (2:30pm - 5:00pm MST), "
    "Saturday, Nov 14: Midnight Run - 🍕 Food Service (11:30pm - 12:30am MST)"
)


def test_split_availability_slots_keeps_each_shift_whole():
    from services.volunteers_service import split_availability_slots
    slots = split_availability_slots(VOLUNTEER_AVAILABILITY)
    assert len(slots) == 3
    assert slots[0].startswith("Saturday, Nov 14: Doors Open & Registration")
    assert split_availability_slots("I'm around all weekend") == []
    assert split_availability_slots("") == []


def test_calendar_attachments_use_the_volunteer_shift_hours():
    """Volunteer shifts carry explicit hours; they used to be dropped (not in the
    mentor mapping) or mis-timed ("Afternoon Support" → the mentor 1-3pm block)."""
    result = get_calendar_email_attachment_from_availability(
        VOLUNTEER_AVAILABILITY, "vol@example.com", volunteer_type="volunteer", year=2026
    )
    assert len(result) == 3
    contents = [a["content"] for a in result]
    # No hackathon timezone passed: "MST" in the slot text → America/Phoenix
    # (UTC-7), written as UTC instants. (The old "…T080000MST" suffix isn't
    # valid iCalendar; clients treated it as floating time.)
    assert "DTSTART:20261114T150000Z" in contents[0]
    assert "DTEND:20261114T180000Z" in contents[0]
    assert "DTSTART:20261114T213000Z" in contents[1]
    assert "DTEND:20261115T000000Z" in contents[1]
    # Midnight run (11:30pm–12:30am MST) spans into the next day.
    assert "DTSTART:20261115T063000Z" in contents[2]
    assert "DTEND:20261115T073000Z" in contents[2]
    assert all("TZID" not in c for c in contents)
    assert all("ATTENDEE:MAILTO:vol@example.com" in c for c in contents)


def test_calendar_attachments_mentor_named_slots_still_use_the_mapping():
    result = get_calendar_email_attachment_from_availability(
        "Saturday, Nov 14: 🌅 Early Morning (7am - 9am MST)", "m@example.com",
        volunteer_type="mentor", year=2026,
    )
    assert len(result) == 1
    assert "DTSTART:20261114T140000Z" in result[0]["content"]


def test_confirmation_email_lists_the_shifts_as_text():
    from services.volunteers_service import split_availability_slots
    sent = _send_confirmation_capturing_html("volunteer", shifts=split_availability_slots(VOLUNTEER_AVAILABILITY))
    assert "Your shifts" in sent["html"]
    assert "Doors Open &amp; Registration - 📋 Check-in Registration (8:00am - 11:00am MST)" in sent["html"]
    assert "Midnight Run" in sent["html"]


def test_confirmation_email_without_shifts_has_no_shift_block():
    sent = _send_confirmation_capturing_html("judge", shifts=[])
    assert "Your shifts" not in sent["html"]


# ---------------------------------------------------------------------------
# Event details come from the hackathon record. The .ics LOCATION used to be
# hardcoded to "Virtual" for every event (and the email body named no event
# at all), so an in-person volunteer's calendar said they were volunteering
# online.
# ---------------------------------------------------------------------------

FALL_2026_EVENT = {
    "event_id": "fall-2026",
    "title": "2026 Fall Hackathon: Tempe, AZ | Opportunity Hack",
    "location": "Engineering Center G Wing, 501 E Tyler Mall, Tempe, AZ",
    "timezone": "America/Phoenix",
    "start_date": "2026-11-14",
    "end_date": "2026-11-15",
}


def test_calendar_attachments_carry_the_venue_and_hackathon_timezone():
    result = get_calendar_email_attachment_from_availability(
        VOLUNTEER_AVAILABILITY, "vol@example.com", volunteer_type="volunteer", year=2026,
        event_name="Opportunity Hack Fall 2026",
        location=FALL_2026_EVENT["location"],
        tz_name="America/Phoenix",
    )
    content = result[0]["content"]
    # Commas inside a TEXT value must be escaped or the venue is truncated.
    assert "LOCATION:Engineering Center G Wing\\, 501 E Tyler Mall\\, Tempe\\, AZ" in content
    assert "Virtual" not in content
    assert "SUMMARY:Opportunity Hack Fall 2026 - Doors Open & Registration - 📋 Check-in Registration" in content
    assert "DTSTART:20261114T150000Z" in content
    assert "times are MST (America/Phoenix)" in content
    assert "DTSTAMP:" in content and "Z\n" in content  # stamps are UTC


def test_calendar_attachments_follow_the_admin_timezone_over_the_slot_label():
    """The hackathon's `timezone` (admin Overview) is the source of truth —
    8:00am in New York is 13:00Z even if the slot text says MST."""
    result = get_calendar_email_attachment_from_availability(
        "Saturday, Nov 14: Doors Open - 📋 Check-in (8:00am - 11:00am MST)", "vol@example.com",
        volunteer_type="volunteer", year=2026, tz_name="America/New_York",
    )
    content = result[0]["content"]
    assert "DTSTART:20261114T130000Z" in content
    assert "DTEND:20261114T160000Z" in content
    assert "times are EST (America/New_York)" in content


def test_calendar_attachments_use_daylight_time_labels_without_a_hackathon_timezone():
    result = get_calendar_email_attachment_from_availability(
        "Saturday, Jul 11: Doors Open - 📋 Check-in (8:00am - 11:00am PDT)", "vol@example.com",
        volunteer_type="volunteer", year=2026,
    )
    # America/Los_Angeles in July is UTC-7.
    assert "DTSTART:20260711T150000Z" in result[0]["content"]


def test_calendar_attachments_survive_an_invalid_hackathon_timezone():
    result = get_calendar_email_attachment_from_availability(
        "Saturday, Nov 14: Doors Open - 📋 Check-in (8:00am - 11:00am MST)", "vol@example.com",
        volunteer_type="volunteer", year=2026, tz_name="Mars/Olympus_Mons",
    )
    # Falls back to the slot's MST → America/Phoenix.
    assert "DTSTART:20261114T150000Z" in result[0]["content"]


def test_calendar_attachments_omit_location_when_the_event_is_unknown():
    result = get_calendar_email_attachment_from_availability(
        "Saturday, Nov 14: 🌅 Early Morning (7am - 9am PST)", "m@example.com",
        volunteer_type="mentor", year=2026,
    )
    content = result[0]["content"]
    assert "LOCATION:" not in content
    assert "Virtual" not in content
    # No hackathon timezone → the abbreviation in the slot text decides.
    assert "DTSTART:20261114T150000Z" in content  # 7am PST = 15:00Z


def test_calendar_event_name_normalizes_hackathon_titles():
    from services.volunteers_service import _calendar_event_name
    assert _calendar_event_name(FALL_2026_EVENT) == "Opportunity Hack Fall 2026"
    assert _calendar_event_name({"title": "Opportunity Hack - ASU Fall Hackathon 2026"}) == "Opportunity Hack Fall 2026"
    assert _calendar_event_name({"title": "Opportunity Hack Summer 2026 Virtual"}) == "Opportunity Hack Summer 2026"
    assert _calendar_event_name({"title": "Arizona Hackathon for Social Good"}) == "Opportunity Hack Arizona Hackathon for Social Good"
    assert _calendar_event_name({"title": ""}) == "Opportunity Hack Volunteering"
    assert _calendar_event_name(None) == "Opportunity Hack Volunteering"


def test_format_event_date_range():
    from services.volunteers_service import _format_event_date_range, _event_year
    assert _format_event_date_range("2026-11-14", "2026-11-15") == "Nov 14–15, 2026"
    assert _format_event_date_range("2026-11-14T00:00:00", "2026-11-14") == "Nov 14, 2026"
    assert _format_event_date_range("2026-11-28", "2026-12-01") == "Nov 28 – Dec 1, 2026"
    assert _format_event_date_range("2026-12-31", "2027-01-01") == "Dec 31, 2026 – Jan 1, 2027"
    assert _format_event_date_range("soon", None) == ""
    assert _event_year(FALL_2026_EVENT) == 2026
    assert _event_year({"start_date": "TBD"}) is None


def test_confirmation_email_names_the_event_dates_and_venue():
    sent = _send_confirmation_capturing_html("volunteer", event=FALL_2026_EVENT)
    html_body = sent["html"]
    assert "Your event" in html_body
    assert "Opportunity Hack Fall 2026" in html_body
    assert "Nov 14–15, 2026" in html_body
    assert "Engineering Center G Wing, 501 E Tyler Mall, Tempe, AZ" in html_body
    assert "https://www.ohack.dev/hack/fall-2026" in html_body
    assert "Virtual" not in html_body


def test_confirmation_email_without_an_event_has_no_event_block():
    sent = _send_confirmation_capturing_html("volunteer", event=None)
    assert "Your event" not in sent["html"]


@patch('services.volunteers_service.get_db')
def test_create_volunteer_feeds_the_hackathon_into_the_email_and_invites(mock_get_db):
    """The hackathon doc for the event_id drives event_name / location /
    timezone / year on the invites and the `event` block on the email."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    hackathon_doc = MagicMock()
    hackathon_doc.to_dict.return_value = dict(FALL_2026_EVENT)
    hackathons = MagicMock()
    hackathons.where.return_value.limit.return_value.stream.return_value = iter([hackathon_doc])
    volunteers = MagicMock()

    def collection(name):
        return hackathons if name == 'hackathons' else volunteers
    mock_db.collection.side_effect = collection

    with patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=None), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.volunteers_service.get_calendar_email_attachment_from_availability',
               return_value=[]) as mock_ics, \
         patch('services.volunteers_service.send_volunteer_confirmation_email') as mock_email:
        create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            volunteer_data={"volunteer_type": "volunteer", "name": "Sam Doe",
                            "availability": VOLUNTEER_AVAILABILITY},
        )

    ics_kwargs = mock_ics.call_args.kwargs
    assert ics_kwargs["event_name"] == "Opportunity Hack Fall 2026"
    assert ics_kwargs["location"] == FALL_2026_EVENT["location"]
    assert ics_kwargs["tz_name"] == "America/Phoenix"
    assert ics_kwargs["year"] == 2026
    assert mock_email.call_args.kwargs["event"]["location"] == FALL_2026_EVENT["location"]


# ---------------------------------------------------------------------------
# Hacker auto-approve with capacity (Nov 2026)
# ---------------------------------------------------------------------------

HACKER_DATA = {"volunteer_type": "hacker", "name": "Riley Hacker"}


def _db_with_hackathon(constraints):
    """MagicMock db whose `hackathons` query yields one doc with `constraints`;
    every other collection is a shared MagicMock (captures volunteer writes)."""
    mock_db = MagicMock()
    hackathon_doc = MagicMock()
    hackathon_doc.to_dict.return_value = {**FALL_2026_EVENT, "constraints": constraints}
    hackathons = MagicMock()
    hackathons.where.return_value.limit.return_value.stream.return_value = iter([hackathon_doc])
    volunteers = MagicMock()
    mock_db.collection.side_effect = lambda name: hackathons if name == 'hackathons' else volunteers
    return mock_db, volunteers


def _submit_hacker(mock_db, existing=None, on_roster=0):
    with patch('services.volunteers_service.get_db', return_value=mock_db), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=existing), \
         patch('services.volunteers_service._count_selected_hackers', return_value=on_roster), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.hackathons_service.get_volunteer_by_event') as mock_by_event, \
         patch('services.volunteers_service.send_volunteer_confirmation_email') as mock_email:
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            volunteer_data=dict(HACKER_DATA),
        )
    return result, mock_email, mock_by_event


def test_auto_approve_puts_a_new_hacker_on_the_roster():
    mock_db, volunteers = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 90})
    result, mock_email, mock_by_event = _submit_hacker(mock_db, on_roster=12)

    assert result["isSelected"] is True
    assert result["status"] == "approved"
    assert result["auto_approved"] is True
    assert result["auto_approved_at"]
    written = volunteers.document.return_value.set.call_args.args[0]
    assert written["isSelected"] is True
    assert mock_email.call_args.kwargs["roster_outcome"] == "approved"
    mock_by_event.cache_clear.assert_called_once()


def test_auto_approve_waitlists_once_capacity_is_reached():
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 90})
    result, mock_email, _ = _submit_hacker(mock_db, on_roster=90)

    assert result["isSelected"] is False
    assert result["status"] == "waitlisted"
    assert result["auto_approved"] is False
    assert mock_email.call_args.kwargs["roster_outcome"] == "waitlisted"


def test_auto_approve_without_capacity_has_no_limit():
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": None})
    result, _, _ = _submit_hacker(mock_db, on_roster=5000)
    assert result["isSelected"] is True


def test_auto_approve_off_leaves_new_hackers_pending():
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": False, "hacker_capacity": 90})
    result, mock_email, mock_by_event = _submit_hacker(mock_db)

    assert result["isSelected"] is False
    assert "status" not in result
    assert "auto_approved" not in result
    assert mock_email.call_args.kwargs["roster_outcome"] is None
    mock_by_event.cache_clear.assert_not_called()


def test_auto_approve_never_applies_to_other_roles():
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True})
    with patch('services.volunteers_service.get_db', return_value=mock_db), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=None), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.volunteers_service.send_volunteer_confirmation_email'):
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            volunteer_data={"volunteer_type": "mentor", "name": "M"},
        )
    assert result["isSelected"] is False


def test_auto_approve_promotes_a_pending_hacker_who_updates():
    existing = {"id": "vol-1", "user_id": MOCK_USER_ID, "email": MOCK_EMAIL, "event_id": "fall-2026",
                "volunteer_type": "hacker", "isSelected": False, "status": "pending"}
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 90})
    result, mock_email, _ = _submit_hacker(mock_db, existing=existing, on_roster=3)

    assert result["isSelected"] is True
    assert result["status"] == "approved"
    assert result["auto_approved"] is True
    assert mock_email.call_args.kwargs["roster_outcome"] == "approved"


@pytest.mark.parametrize("status", ["denied", "Withdrew", "no_show"])
def test_auto_approve_never_overrides_a_staff_decision(status):
    existing = {"id": "vol-1", "user_id": MOCK_USER_ID, "email": MOCK_EMAIL, "event_id": "fall-2026",
                "volunteer_type": "hacker", "isSelected": False, "status": status}
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True})
    result, mock_email, _ = _submit_hacker(mock_db, existing=existing)

    assert result["isSelected"] is False
    assert result["status"] == status
    assert "auto_approved" not in result
    assert mock_email.call_args.kwargs["roster_outcome"] is None


def test_update_of_a_pending_hacker_at_capacity_keeps_their_status():
    existing = {"id": "vol-1", "user_id": MOCK_USER_ID, "email": MOCK_EMAIL, "event_id": "fall-2026",
                "volunteer_type": "hacker", "isSelected": False, "status": "pending"}
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 2})
    result, _, _ = _submit_hacker(mock_db, existing=existing, on_roster=2)

    assert result["isSelected"] is False
    assert result["status"] == "pending"


def test_update_of_an_approved_hacker_does_not_recount_or_restamp():
    existing = {"id": "vol-1", "user_id": MOCK_USER_ID, "email": MOCK_EMAIL, "event_id": "fall-2026",
                "volunteer_type": "hacker", "isSelected": True, "status": "approved"}
    mock_db, _ = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 2})
    with patch('services.volunteers_service._hacker_roster_decision') as decide:
        result, mock_email, _ = _submit_hacker(mock_db, existing=existing, on_roster=99)
    decide.assert_not_called()
    assert result["isSelected"] is True
    assert "auto_approved_at" not in result
    assert mock_email.call_args.kwargs["roster_outcome"] is None


def test_applicant_cannot_claim_auto_approved_fields():
    mock_db, _ = _db_with_hackathon({})
    with patch('services.volunteers_service.get_db', return_value=mock_db), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=None), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.volunteers_service.send_volunteer_confirmation_email'):
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            volunteer_data={**HACKER_DATA, "auto_approved": True, "auto_approved_at": "2026-11-14T08:00:00",
                            "isSelected": True},
        )
    assert result["isSelected"] is False
    assert "auto_approved" not in result
    assert "auto_approved_at" not in result


def test_count_selected_hackers_falls_back_without_aggregation():
    """MockFirestore has no count(): the helper streams ids instead."""
    from services.volunteers_service import _count_selected_hackers
    query = MagicMock()
    query.count.side_effect = AttributeError("no count()")
    query.select.return_value.stream.return_value = iter([object(), object(), object()])
    mock_db = MagicMock()
    mock_db.collection.return_value.where.return_value.where.return_value.where.return_value = query
    with patch('services.volunteers_service.get_db', return_value=mock_db):
        assert _count_selected_hackers("fall-2026") == 3


def test_confirmation_email_for_an_auto_approved_hacker():
    import os
    from services import volunteers_service
    sent = {}
    with patch("services.volunteers_service._notifications_disabled", return_value=False), \
         patch.dict(os.environ, {"RESEND_WELCOME_EMAIL_KEY": "re_dummy"}), \
         patch("services.volunteers_service.resend.Emails.send", side_effect=lambda p: sent.update(p) or {"id": "re_test"}):
        volunteers_service.send_volunteer_confirmation_email(
            "Riley", "Hacker", "r@example.com", "hacker", None, "fall-2026", event=FALL_2026_EVENT,
            roster_outcome="approved",
        )
    assert sent["subject"].startswith("[Confirmed]")
    assert "on the roster" in sent["html"]
    assert "https://www.ohack.dev/hack/fall-2026/hacker-application" in sent["html"]
    assert "https://www.ohack.dev/hack/fall-2026/manageteam" in sent["html"]
    assert "review it shortly" not in sent["html"]


def test_confirmation_email_for_a_waitlisted_hacker():
    sent = _send_confirmation_capturing_html("hacker")  # baseline: received copy
    assert "review it shortly" in sent["html"]
    assert "waitlist" not in sent["html"]

    import os
    from services import volunteers_service
    sent = {}
    with patch("services.volunteers_service._notifications_disabled", return_value=False), \
         patch.dict(os.environ, {"RESEND_WELCOME_EMAIL_KEY": "re_dummy"}), \
         patch("services.volunteers_service.resend.Emails.send", side_effect=lambda p: sent.update(p) or {"id": "re_test"}):
        volunteers_service.send_volunteer_confirmation_email(
            "Riley", "Hacker", "r@example.com", "hacker", None, "fall-2026", roster_outcome="waitlisted",
        )
    assert "waitlist" in sent["html"]
    assert "[Confirmed]" not in sent["subject"]


# ---------------------------------------------------------------------------
# Server-owned fields on create (Nov 2026, code-review item 3)
#
# The create branch used to build the doc from the token and then
# volunteer_doc.update(body), so a body could overwrite id / user_id /
# created_by and submit its own review status. With hacker auto-approve on,
# one logged-in user could POST with a fresh user_id + email each time, miss
# the existing-application lookup, and fill the roster to capacity.
# ---------------------------------------------------------------------------

def test_create_ignores_body_identity_and_review_fields():
    mock_db, volunteers = _db_with_hackathon({})
    with patch('services.volunteers_service.get_db', return_value=mock_db), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=None), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.volunteers_service.send_volunteer_confirmation_email'):
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            created_by=MOCK_USER_ID,
            volunteer_data={
                **HACKER_DATA,
                "id": "attacker-chosen-id",
                "user_id": "someone-else",
                "event_id": "another-event",
                "status": "approved",
                "created_by": "someone-else",
                "created_timestamp": "2020-01-01T00:00:00",
                "timestamp": "2020-01-01T00:00:00",
            },
        )

    written = volunteers.document.return_value.set.call_args.args[0]
    doc_id = volunteers.document.call_args.args[0]
    for doc in (written, result):
        assert doc["id"] == doc_id != "attacker-chosen-id"
        assert doc["user_id"] == MOCK_USER_ID
        assert doc["event_id"] == "fall-2026"
        assert doc["created_by"] == MOCK_USER_ID
        assert doc["created_timestamp"] != "2020-01-01T00:00:00"
        assert doc["timestamp"] != "2020-01-01T00:00:00"
        assert "status" not in doc
        assert doc["isSelected"] is False
    assert written["name"] == "Riley Hacker"  # the rest of the form still saves


def test_create_status_comes_only_from_the_roster_decision():
    mock_db, volunteers = _db_with_hackathon({"hacker_auto_approve": True, "hacker_capacity": 90})
    with patch('services.volunteers_service.get_db', return_value=mock_db), \
         patch('services.volunteers_service.find_volunteer_by_caller_identity', return_value=None), \
         patch('services.volunteers_service._count_selected_hackers', return_value=90), \
         patch('services.volunteers_service.get_slack_user_by_email', return_value=None), \
         patch('services.volunteers_service.send_admin_notification_email'), \
         patch('services.volunteers_service.send_slack_volunteer_notification'), \
         patch('services.hackathons_service.get_volunteer_by_event'), \
         patch('services.volunteers_service.send_volunteer_confirmation_email'):
        result = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email=MOCK_EMAIL, event_id="fall-2026",
            volunteer_data={**HACKER_DATA, "status": "approved"},
        )
    assert result["status"] == "waitlisted"
    assert result["isSelected"] is False


def test_second_submit_by_same_login_with_new_email_updates_not_creates():
    """The token user_id finds the first application even when the body's
    email and user_id change — so one login gets one application (and one
    auto-approval) per event."""
    from mockfirestore import MockFirestore

    db = MockFirestore()
    db.collection('hackathons').document('h1').set({
        **FALL_2026_EVENT,
        "constraints": {"hacker_auto_approve": True, "hacker_capacity": 90},
    })
    common = [
        patch('services.volunteers_service.get_db', return_value=db),
        patch('services.volunteers_service.get_volunteer_by_user_id', return_value=None),
        patch('services.volunteers_service.get_volunteer_by_email', return_value=None),
        patch('services.users_service.get_propel_user_details_by_id', return_value=None),
        patch('services.volunteers_service.get_slack_user_by_email', return_value=None),
        patch('services.volunteers_service.send_admin_notification_email'),
        patch('services.volunteers_service.send_slack_volunteer_notification'),
        patch('services.hackathons_service.get_volunteer_by_event'),
        patch('services.volunteers_service.send_volunteer_confirmation_email'),
    ]
    for p in common:
        p.start()
    try:
        first = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email="one@example.com", event_id="fall-2026",
            volunteer_data={**HACKER_DATA, "email": "one@example.com", "user_id": "fake-1"},
        )
        second = create_or_update_volunteer(
            user_id=MOCK_USER_ID, email="two@example.com", event_id="fall-2026",
            volunteer_data={**HACKER_DATA, "email": "two@example.com", "user_id": "fake-2"},
        )
    finally:
        patch.stopall()

    docs = [d.to_dict() for d in db.collection('volunteers').stream()]
    assert len(docs) == 1
    assert second["id"] == first["id"]
    assert docs[0]["user_id"] == MOCK_USER_ID
    assert docs[0]["email"] == "one@example.com"
