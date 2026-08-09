"""Profile read/write must survive an OAuth-provider outage.

get_profile_metadata / save_profile_metadata used to depend SOLELY on the live
OAuth round-trip (like volunteering once did — see test_volunteer_resolve.py).
They now resolve through _resolve_and_ensure_user, whose tier-1 path (stored
propel_id) makes no external call.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")  # -> MockFirestore; no network at import

import services.users_service as us
from db.db import get_db


def _boom(_propel_id):
    raise AssertionError("OAuth round-trip must NOT be required when propel_id resolves")


def _seed_user(db_id, propel_id):
    get_db().collection("users").document(db_id).set({
        "user_id": f"oauth2|slack|T123-U{db_id[:6].upper()}",
        "email_address": f"{db_id[:8]}@example.com",
        "profile_image": "https://i.imgur.com/RdOsE7s.png",
        "name": "Identity Test",
        "nickname": "Ident",
        "propel_id": propel_id,
        "badges": [],
        "teams": [],
    })


def test_profile_read_works_with_oauth_down(monkeypatch):
    db_id = "ident111ident111ident111ident111"
    propel_id = "propel-ident-read-unique"
    _seed_user(db_id, propel_id)

    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    monkeypatch.setattr(us, "get_propel_user_details_by_id", _boom)
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)

    result = us.get_profile_metadata(propel_id)
    assert result is not None
    assert result["id"] == db_id
    assert result["name"] == "Identity Test"


def test_profile_save_works_with_oauth_down(monkeypatch):
    db_id = "ident222ident222ident222ident222"
    propel_id = "propel-ident-save-unique"
    _seed_user(db_id, propel_id)

    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)

    result = us.save_profile_metadata(propel_id, {"metadata": {"company": "Resilient Corp"}})
    assert result is not None
    assert result["company"] == "Resilient Corp"

    saved = get_db().collection("users").document(db_id).get().to_dict()
    assert saved["company"] == "Resilient Corp"
    # Untouched fields survive the save
    assert saved["name"] == "Identity Test"


def test_profile_save_without_metadata_key_returns_none(monkeypatch):
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)
    assert us.save_profile_metadata("propel-ident-bad-unique", {}) is None
