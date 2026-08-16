"""Parity: the legacy delegates serve EXACTLY the canonical service's data.

If these fail, the two URL families have diverged — the precise bug class the
profile-stack retirement exists to kill.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import api.messages.messages_service as ms
import services.users_service as us
from db.db import get_db

from api.messages.tests.test_profile_characterization import LEGACY_PROFILE_KEYS


DB_ID = "par11111par11111par11111par11111"
PROPEL_ID = "propel-parity-unique"


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    get_db().collection("users").document(DB_ID).set({
        "user_id": "oauth2|slack|T123-UPAR1",
        "email_address": "parity@example.com",
        "profile_image": "https://i.imgur.com/RdOsE7s.png",
        "name": "Parity Test",
        "nickname": "Parity",
        "propel_id": PROPEL_ID,
        "role": "mentor",
        "city": "Mesa",
        "badges": [],
        "teams": [],
    })
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(us, "get_propel_user_details_by_id",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("no oauth")))
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("no oauth")))
    yield


def _without_login_stamp(d):
    # Every GET stamps a fresh last_login — irrelevant to shape parity
    return {k: v for k, v in d.items() if k != "last_login"}


def test_legacy_get_equals_canonical_get():
    us.get_profile_metadata.cache_clear()
    canonical = us.get_profile_metadata(PROPEL_ID)

    ms.get_profile_metadata_old.cache_clear()
    us.get_profile_metadata.cache_clear()
    legacy = ms.get_profile_metadata_old(PROPEL_ID)

    assert _without_login_stamp(legacy["text"]) == _without_login_stamp(canonical)
    assert LEGACY_PROFILE_KEYS <= set(legacy["text"].keys())


def test_legacy_by_id_equals_canonical_by_id():
    legacy = ms.get_user_by_id_old(DB_ID)
    canonical = us.get_profile_by_db_id(DB_ID)
    assert legacy == canonical


def test_write_via_legacy_visible_via_canonical():
    result = ms.save_profile_metadata_old(PROPEL_ID, {"metadata": {"headline": "Parity headline"}})
    assert result is not None

    us.get_profile_metadata.cache_clear()
    canonical = us.get_profile_metadata(PROPEL_ID)
    assert canonical["headline"] == "Parity headline"
