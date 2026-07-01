"""Tests for the volunteering identity-resolution fix.

Regression coverage for the "Couldn't log that time" bug: the volunteering
WRITE used to depend solely on the live Slack/Google OAuth round-trip
(get_oauth_user_from_propel_user_id). When that returned None (expired/
unavailable provider token, PropelAuth hiccup, or its negative cache), the
write 404'd even though the user clearly existed.

_resolve_and_ensure_user now resolves in this order, so a broken provider token
can never block volunteering:
  1. by stored propel_id (no external call),
  2. OAuth provider round-trip (+ lazy-create),
  3. PropelAuth user-metadata fallback (reliable, no provider token) — resolve
     an existing doc by email (backfill propel_id) or lazily create one.
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


def test_metadata_fallback_resolves_existing_by_email_and_backfills_propel_id(monkeypatch):
    """OAuth down + no propel_id match -> resolve the existing doc by email and
    backfill propel_id so future requests hit the fast path."""
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: None)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda pid: None)  # OAuth broken
    monkeypatch.setattr(us, "_fetch_propel_metadata", lambda pid: {
        "email": "greg@ohack.org", "name": "Greg", "nickname": "Greg", "profile_image": ""})

    existing = User()
    existing.user_id = "oauth2|slack|T123-UOLD"
    existing.propel_id = None  # never had propel_id set
    monkeypatch.setattr(us, "fetch_user_by_email", lambda email: existing if email == "greg@ohack.org" else None)

    backfilled = {}
    monkeypatch.setattr(us, "upsert_profile_metadata", lambda u: backfilled.update(propel_id=u.propel_id))

    resolved, user_id = us._resolve_and_ensure_user("00216ceb-82ff-4528-ad11-dd45d02a0fa6")
    assert resolved is existing
    assert user_id == "oauth2|slack|T123-UOLD"
    assert existing.propel_id == "00216ceb-82ff-4528-ad11-dd45d02a0fa6"
    assert backfilled.get("propel_id") == "00216ceb-82ff-4528-ad11-dd45d02a0fa6"


def test_metadata_fallback_creates_doc_when_none_exists(monkeypatch):
    """Brand-new user + OAuth down -> create a doc from PropelAuth metadata."""
    state = {"created": None}
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: state["created"])  # None until created
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda pid: None)
    monkeypatch.setattr(us, "fetch_user_by_email", lambda email: None)
    monkeypatch.setattr(us, "_fetch_propel_metadata", lambda pid: {
        "email": "new@ohack.org", "name": "New Person", "nickname": "New", "profile_image": "http://img"})

    def fake_save_user(**kwargs):
        u = User()
        u.user_id = kwargs.get("user_id")
        u.email_address = kwargs.get("email")
        u.propel_id = kwargs.get("propel_id")
        state["created"] = u
        return u
    monkeypatch.setattr(us, "save_user", fake_save_user)

    resolved, user_id = us._resolve_and_ensure_user("propel-brand-new")
    assert resolved is state["created"]
    assert resolved.propel_id == "propel-brand-new"
    assert resolved.email_address == "new@ohack.org"


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
        "timestamp": "2026-06-29T19:00:00.000Z",
    })

    assert res is user
    entry = user.volunteering[-1]
    assert entry["commitmentHours"] == 4
    assert entry["finalHours"] == 4
    assert entry["manual"] is True
    assert entry["reason"] == "coding"
    assert entry["timestamp"] == "2026-06-29T19:00:00.000Z"


def test_returns_none_when_identity_unresolvable(monkeypatch):
    monkeypatch.setattr(us, "fetch_user_by_propel_id", lambda pid: None)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda pid: None)
    monkeypatch.setattr(us, "_fetch_propel_metadata", lambda pid: None)  # metadata also unavailable

    user, user_id = us._resolve_and_ensure_user("ghost")
    assert user is None and user_id is None
    # And a write with no resolvable identity returns None (-> 404 at the view).
    assert us.save_volunteering_time("ghost", {"finalHours": 1, "reason": "coding"}) is None
