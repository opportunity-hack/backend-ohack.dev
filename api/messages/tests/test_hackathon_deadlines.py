"""
Tests for the hackathon `deadlines` object: validation (unknown key, ordering,
naive->offset normalization) and the save_hackathon persistence path (DELETE_FIELD
on update for an explicit null, dropped entirely on create).

Mirrors the mocking pattern in api/messages/tests/test_cache_invalidation.py
(patch services.hackathons_service._get_db + clear_cache; a MagicMock stands
in for the firestore.Transaction passed into the @firestore.transactional
inner function — google-cloud-firestore's _Transactional wrapper only touches
attributes on it, never asserts a real Transaction type).
"""
import pytest
from unittest.mock import patch, MagicMock
from firebase_admin import firestore

from common.utils.validators import (
    DEADLINE_KEYS,
    normalize_deadline_iso,
    validate_deadlines,
)
from services.hackathons_service import save_hackathon


# ---------------------------------------------------------------------------
# normalize_deadline_iso / validate_deadlines — pure validator behavior.
# (Broader validator-suite coverage lives in test/common/utils/test_validators.py;
# these are the deadlines-specific cases called out by the plan.)
# ---------------------------------------------------------------------------

def test_normalize_deadline_iso_naive_localizes_to_tz():
    result = normalize_deadline_iso("2026-10-10T15:00:00", "America/Phoenix")
    assert result == "2026-10-10T15:00:00-07:00"


def test_normalize_deadline_iso_trailing_z_becomes_offset():
    result = normalize_deadline_iso("2026-10-10T22:00:00Z", "America/Phoenix")
    assert result == "2026-10-10T22:00:00+00:00"


def test_normalize_deadline_iso_none_and_empty_string_clear():
    assert normalize_deadline_iso(None) is None
    assert normalize_deadline_iso("") is None


def test_normalize_deadline_iso_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_deadline_iso("not-a-date")


def test_validate_deadlines_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unknown deadlines key"):
        validate_deadlines({"submitted_by": "2026-10-10T15:00:00"}, "America/Phoenix")


def test_validate_deadlines_enforces_submission_ordering():
    with pytest.raises(ValueError, match="late_submission_until must be on or after submission"):
        validate_deadlines(
            {
                "submission": "2026-10-10T15:00:00",
                "late_submission_until": "2026-10-10T10:00:00",
            },
            "America/Phoenix",
        )


def test_validate_deadlines_enforces_voting_ordering():
    with pytest.raises(ValueError, match="voting_closes must be after voting_opens"):
        validate_deadlines(
            {
                "voting_opens": "2026-10-12T00:00:00",
                "voting_closes": "2026-10-12T00:00:00",
            },
            "America/Phoenix",
        )


def test_validate_deadlines_accepts_full_valid_object():
    result = validate_deadlines(
        {
            "submission": "2026-10-10T15:00:00",
            "late_submission_until": "2026-10-10T18:00:00",
            "voting_opens": "2026-10-10T18:00:00",
            "voting_closes": "2026-10-12T23:59:59",
        },
        "America/Phoenix",
    )
    assert set(result.keys()) == DEADLINE_KEYS


def test_validate_deadlines_preserves_explicit_none_as_clear_intent():
    result = validate_deadlines({"submission": None}, "America/Phoenix")
    assert result == {"submission": None}


# ---------------------------------------------------------------------------
# save_hackathon persistence — DELETE_FIELD on update, dropped on create.
# ---------------------------------------------------------------------------

def _base_json(**extra):
    data = {
        "title": "Test Hackathon",
        "description": "Test Description",
        "location": "Test Location",
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "type": "hackathon",
        "image_url": "https://example.com/image.png",
        "event_id": "event123",
    }
    data.update(extra)
    return data


def _mock_db():
    mock_db_instance = MagicMock()
    mock_transaction = MagicMock()
    mock_db_instance.transaction.return_value = mock_transaction
    mock_hackathon_ref = MagicMock()
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_hackathon_ref
    mock_db_instance.collection.return_value = mock_collection
    return mock_db_instance, mock_transaction


@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_create_drops_none_deadline_keys(mock_get_db, mock_clear_cache):
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    json_data = _base_json(deadlines={"submission": "2026-01-01T15:00:00", "voting_opens": None})
    save_hackathon(json_data, "user123")

    written = mock_transaction.set.call_args[0][1]
    assert written["deadlines"] == {"submission": "2026-01-01T15:00:00-07:00"}
    mock_clear_cache.assert_called_once()


@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_update_writes_delete_field_for_none(mock_get_db, mock_clear_cache):
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    json_data = _base_json(
        id="abc123",
        deadlines={"submission": None, "late_submission_until": "2026-01-02T12:00:00-07:00"},
    )
    save_hackathon(json_data, "user123")

    written = mock_transaction.set.call_args[0][1]
    assert written["deadlines"]["submission"] is firestore.DELETE_FIELD
    assert written["deadlines"]["late_submission_until"] == "2026-01-02T12:00:00-07:00"
    # merge=True on update so untouched fields on the doc survive
    assert mock_transaction.set.call_args.kwargs.get("merge") is True


@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_without_deadlines_key_omits_it(mock_get_db, mock_clear_cache):
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    save_hackathon(_base_json(), "user123")

    written = mock_transaction.set.call_args[0][1]
    assert "deadlines" not in written


@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_skips_deadlines_with_unknown_key_but_keeps_rest(mock_get_db, mock_clear_cache):
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    result = save_hackathon(_base_json(deadlines={"bogus_key": "2026-01-01T15:00:00"}), "user123")

    written = mock_transaction.set.call_args[0][1]
    assert "deadlines" not in written
    assert getattr(result, "skipped_fields", None)
    assert any(s["field"] == "deadlines" for s in result.skipped_fields)


# ---------------------------------------------------------------------------
# LOW finding #13: `deadlines: {}` (and `deadlines: null`) on an UPDATE write
# an empty map under set(merge=True) — Firestore's merge semantics only
# touch the sub-fields actually present in the map you send, so an empty map
# merges zero keys into the existing `deadlines` map and leaves it entirely
# untouched (a no-op), rather than clearing it. This locks in that actual
# behavior with a test and documents it (see api/submissions/README.md and
# this repo's CLAUDE.md) so "to clear a single deadline send {key: null};
# sending {} is a no-op" isn't just tribal knowledge.
# ---------------------------------------------------------------------------

@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_update_empty_deadlines_dict_is_noop(mock_get_db, mock_clear_cache):
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    save_hackathon(_base_json(id="abc123", deadlines={}), "user123")

    written = mock_transaction.set.call_args[0][1]
    # An empty map is what gets sent to Firestore; under merge=True this
    # merges zero sub-fields into the existing `deadlines` map, so nothing on
    # the stored doc actually changes.
    assert written["deadlines"] == {}
    assert mock_transaction.set.call_args.kwargs.get("merge") is True


@patch("services.hackathons_service.clear_cache")
@patch("services.hackathons_service._get_db")
def test_save_hackathon_update_top_level_null_deadlines_is_also_a_noop(mock_get_db, mock_clear_cache):
    """A top-level `deadlines: null` (as opposed to a specific key inside the
    object being null) behaves identically to `deadlines: {}` — both end up
    writing an empty map under merge, since save_hackathon does
    `data["deadlines"] or {}` before building the DELETE_FIELD map."""
    mock_db_instance, mock_transaction = _mock_db()
    mock_get_db.return_value = mock_db_instance

    save_hackathon(_base_json(id="abc123", deadlines=None), "user123")

    written = mock_transaction.set.call_args[0][1]
    assert written["deadlines"] == {}
