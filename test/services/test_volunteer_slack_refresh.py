import pytest
from unittest.mock import patch, MagicMock

# --- refresh_volunteer_slack_ids -------------------------------------------

from mockfirestore import MockFirestore
from services.volunteer_slack_refresh import refresh_volunteer_slack_ids, SlackUnavailableError


def _member(slack_id, email, **extra):
    return {"id": slack_id, "name": slack_id.lower(), "profile": {"email": email}, **extra}


@pytest.fixture
def refresh_env():
    """MockFirestore + patched Slack/users lookups; yields (db, mocks)."""
    db = MockFirestore()
    with patch('services.volunteer_slack_refresh.get_db', return_value=db), \
         patch('services.volunteer_slack_refresh.clear_pattern') as clear, \
         patch('services.volunteer_slack_refresh.userlist') as ul, \
         patch('services.volunteer_slack_refresh.get_users_by_emails', return_value={}) as by_emails, \
         patch('services.volunteer_slack_refresh._users_by_propel_ids', return_value={}) as by_propel, \
         patch('services.volunteer_slack_refresh._clear_volunteer_list_cache') as cached:
        ul.return_value = {"members": [
            _member("U1", "Ada@Example.com"),
            _member("U2", "slack-only@example.com"),
            _member("B1", "bot@example.com", is_bot=True),
            _member("U9", "gone@example.com", deleted=True),
        ]}
        yield db, {"clear": clear, "userlist": ul, "by_emails": by_emails,
                   "by_propel": by_propel, "cached": cached}


def _vol(db, doc_id, **fields):
    db.collection("volunteers").document(doc_id).set(
        {"event_id": "ev1", "volunteer_type": "mentor", "name": doc_id, **fields})


def _get(db, doc_id):
    return db.collection("volunteers").document(doc_id).get().to_dict()


def test_refresh_matches_application_email_case_insensitively(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="ada@example.com")

    result = refresh_volunteer_slack_ids("ev1")

    assert _get(db, "v1")["slack_user_id"] == "U1"
    assert _get(db, "v1")["slack_user_id_matched_by"] == "email"
    assert [u["id"] for u in result["updated"]] == ["v1"]
    assert result["unmatched"] == []
    mocks["clear"].assert_called_once_with("slack:userlist:*")
    mocks["cached"].assert_called_once()


def test_refresh_leaves_existing_ids_alone(refresh_env):
    db, _ = refresh_env
    _vol(db, "v1", email="ada@example.com", slack_user_id="UOLD")

    result = refresh_volunteer_slack_ids("ev1")

    assert _get(db, "v1")["slack_user_id"] == "UOLD"
    assert result == {"checked": 1, "updated": [], "unmatched": []}


def test_refresh_skips_bots_and_deleted_members(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="bot@example.com")
    _vol(db, "v2", email="gone@example.com")

    result = refresh_volunteer_slack_ids("ev1")

    assert result["updated"] == []
    assert sorted(u["id"] for u in result["unmatched"]) == ["v1", "v2"]
    assert "slack_user_id" not in _get(db, "v1")
    mocks["cached"].assert_not_called()


def test_refresh_uses_slack_login_on_users_doc_found_by_email(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="google@example.com")
    mocks["by_emails"].return_value = {
        "google@example.com": {"user_id": "oauth2|slack|T1Q7936BH-U2", "email_address": "google@example.com"}
    }

    result = refresh_volunteer_slack_ids("ev1")

    assert _get(db, "v1")["slack_user_id"] == "U2"
    assert result["updated"][0]["matched_by"] == "profile"


def test_refresh_uses_users_doc_found_by_propel_id(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="google@example.com", user_id="propel-uuid-1")
    _vol(db, "v2", email="ada@example.com", user_id="propel-uuid-2")
    mocks["by_propel"].return_value = {
        "propel-uuid-1": {"user_id": "oauth2|google-oauth2|123", "email_address": "slack-only@example.com"}
    }

    refresh_volunteer_slack_ids("ev1")

    # ONE batched lookup, and only for people email alone couldn't place.
    mocks["by_propel"].assert_called_once()
    assert mocks["by_propel"].call_args[0][1] == ["propel-uuid-1"]
    assert _get(db, "v1")["slack_user_id"] == "U2"
    assert _get(db, "v2")["slack_user_id"] == "U1"


def test_refresh_dry_run_reports_matches_without_writing(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="ada@example.com")

    result = refresh_volunteer_slack_ids("ev1", dry_run=True)

    assert [u["slack_user_id"] for u in result["updated"]] == ["U1"]
    assert "slack_user_id" not in _get(db, "v1")
    mocks["cached"].assert_not_called()


def test_users_by_propel_ids_batches_and_keys_by_propel_id():
    from services.volunteer_slack_refresh import _users_by_propel_ids
    db = MockFirestore()
    db.collection("users").document("a").set({"propel_id": "p1", "user_id": "oauth2|slack|T-U1"})
    db.collection("users").document("b").set({"propel_id": "p2", "user_id": "x"})

    found = _users_by_propel_ids(db, ["p1", None, "p1", "p3"])

    assert list(found) == ["p1"]
    assert found["p1"]["user_id"] == "oauth2|slack|T-U1"


def test_refresh_ignores_slack_login_that_left_the_workspace(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="google@example.com")
    mocks["by_emails"].return_value = {
        "google@example.com": {"user_id": "oauth2|slack|T1Q7936BH-U9", "email_address": "google@example.com"}
    }

    result = refresh_volunteer_slack_ids("ev1")

    assert result["updated"] == []
    assert "slack_user_id" not in _get(db, "v1")


def test_refresh_scopes_to_event_and_type(refresh_env):
    db, _ = refresh_env
    _vol(db, "v1", email="ada@example.com", volunteer_type="judge")
    _vol(db, "v2", email="ada@example.com", event_id="other")

    result = refresh_volunteer_slack_ids("ev1", "mentor")

    assert result == {"checked": 0, "updated": [], "unmatched": []}
    assert "slack_user_id" not in _get(db, "v1")
    assert "slack_user_id" not in _get(db, "v2")


def test_refresh_raises_and_writes_nothing_when_slack_fails(refresh_env):
    db, mocks = refresh_env
    _vol(db, "v1", email="ada@example.com")
    mocks["userlist"].side_effect = RuntimeError("slack down")

    with pytest.raises(SlackUnavailableError):
        refresh_volunteer_slack_ids("ev1")

    assert "slack_user_id" not in _get(db, "v1")
