"""Round-trip guarantee: EVERY owner-editable profile field survives
POST /profile -> GET /profile.

This is the test that makes the "saves fine, renders blank" bug class
structurally impossible: if a field is in the registry but any layer drops it
(write set, read path, serializer), this fails.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.users_service as us
from db.db import get_db
from model.user import OWNER_EDITABLE_FIELDS


def _sentinel_for(field):
    if field == "portfolio_links":
        return (
            [{"label": "Site", "url": "https://example.com/rt"}],
            [{"label": "Site", "url": "https://example.com/rt"}],
        )
    if field == "want_stickers":
        return (True, True)
    if field == "expertise":
        return (["Data Science", "Mentor"], ["Data Science", "Mentor"])
    if field == "linkedin_url":
        return ("https://www.linkedin.com/in/roundtrip", "https://www.linkedin.com/in/roundtrip")
    sent = f"rt-{field}"
    return (sent, sent)


DB_ID = "rt111111rt111111rt111111rt111111"
PROPEL_ID = "propel-roundtrip-unique"


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    get_db().collection("users").document(DB_ID).set({
        "user_id": "oauth2|slack|T123-URT1",
        "email_address": "roundtrip@example.com",
        "profile_image": "https://i.imgur.com/RdOsE7s.png",
        "name": "Round Trip",
        "nickname": "RT",
        "propel_id": PROPEL_ID,
        "badges": [],
        "teams": [],
    })
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(us, "get_propel_user_details_by_id",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("no oauth in tests")))
    yield


@pytest.mark.parametrize("field", OWNER_EDITABLE_FIELDS)
def test_every_editable_field_survives_write_then_read(field):
    submitted, expected = _sentinel_for(field)

    save_result = us.save_profile_metadata(PROPEL_ID, {"metadata": {field: submitted}})
    assert save_result is not None, f"save failed for {field}"
    assert save_result.get(field) == expected, f"save response dropped {field}"

    us.get_profile_metadata.cache_clear()
    read_result = us.get_profile_metadata(PROPEL_ID)
    assert read_result is not None
    assert read_result.get(field) == expected, (
        f"{field} was saved but came back {read_result.get(field)!r} — a projection dropped it"
    )
