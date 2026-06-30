"""Tests for the volunteering identity-resolution fix.

Regression coverage for the "Couldn't log that time" bug: the volunteering
WRITE used to depend solely on the live Slack/Google OAuth round-trip
(get_oauth_user_from_propel_user_id). When that returned None (expired/
unavailable provider token, PropelAuth hiccup, or its negative cache), the
write 404'd even though the user clearly existed. _resolve_and_ensure_user now
resolves by the stable, locally-stored propel_id first and only falls back to
the OAuth path, so an OAuth failure no longer blocks logging time.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")  # -> MockFirestore; no network at import

import services.users_service as us
from model.user import User


def _boom(_propel_id):
    raise AssertionError("OAuth round-trip must NOT be called when propel_id resolves")


def test_resolves_by_propel_id_without_oauth(monkeypatch):
    user = User()
    user.user_id = "oauth2|slack|T123-UABC"
    user.propel_id = "propel-uuid-xyz"
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: user if pid == "propel-uuid-xyz" else None)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)

    resolved, user_id = us._resolve_and_ensure_user("propel-uuid-xyz")
    assert resolved is user
    assert user_id == "oauth2|slack|T123-UABC"


def test_falls_back_to_oauth_when_no_propel_match(monkeypatch):
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: None)
    user = User()
    user.user_id = "oauth2|slack|T123-UDEF"
    monkeypatch.setattr(
        us, "get_oauth_user_from_propel_user_id",
        lambda pid: {"sub": "oauth2|slack|T123-UDEF", "email": "a@b.c", "name": "A", "given_name": "A"},
    )
    monkeypatch.setattr(us, "fetch_user_by_user_id", lambda uid: user)

    resolved, user_id = us._resolve_and_ensure_user("propel-no-doc")
    assert resolved is user
    assert user_id == "oauth2|slack|T123-UDEF"


def test_manual_log_saves_combined_entry_without_oauth(monkeypatch):
    user = User()
    user.user_id = "oauth2|slack|T123-UGHI"
    user.propel_id = "propel-manual"
    user.volunteering = []
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: user)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    monkeypatch.setattr(us, "upsert_profile_metadata", lambda u: None)

    res = us.save_volunteering_time("propel-manual", {
        "commitmentHours": 4,
        "finalHours": 4,
        "reason": "coding",
        "manual": True,
        "timestamp": "2026-06-29T10:00:00.000Z",
    })

    assert res is user
    entry = user.volunteering[-1]
    assert entry["commitmentHours"] == 4
    assert entry["finalHours"] == 4
    assert entry["manual"] is True
    assert entry["reason"] == "coding"
    assert entry["timestamp"] == "2026-06-29T10:00:00.000Z"


def test_returns_none_when_identity_unresolvable(monkeypatch):
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: None)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda pid: None)

    user, user_id = us._resolve_and_ensure_user("ghost")
    assert user is None and user_id is None
    # And a write with no resolvable identity returns None (-> 404 at the view).
    assert us.save_volunteering_time("ghost", {"finalHours": 1, "reason": "coding"}) is None
