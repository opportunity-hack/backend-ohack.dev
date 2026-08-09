"""Characterization tests for the LEGACY profile routes (`_old` delegates).

Originally pinned the hand-built legacy bodies; the `_old` functions are now
thin delegates onto the canonical users-service stack, and these tests pin
the OBSERVABLE legacy contract that must survive the delegation:
  - GET keeps the {"text": ...} envelope and every historical key
  - partial saves never clobber unrelated fields
  - the public by-id route stays flat and PII-free
Deleted together with the legacy routes in the final cleanup PR.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")  # -> MockFirestore; no network at import

import pytest

import api.messages.messages_service as ms
import services.users_service as us
from db.db import get_db

# The exact key set the legacy editor read path has always returned.
LEGACY_PROFILE_KEYS = frozenset({
    "id", "user_id", "profile_image", "email_address", "history", "badges",
    "hackathons", "hackathon_history",
    "expertise", "education", "shirt_size", "linkedin_url", "instagram_url",
    "github", "why", "role", "company", "propel_id",
    "street_address", "street_address_2", "city", "state", "postal_code",
    "country", "want_stickers",
    "bio", "headline", "bio_video_url", "portfolio_links",
    "profile_slug", "profile_visibility",
})


def _boom(_propel_id):
    raise AssertionError("OAuth round-trip must not be required (propel_id resolves)")


def _seed_user(db_id, user_id, email, propel_id=None, **extra):
    doc = {
        "user_id": user_id,
        "email_address": email,
        "profile_image": "https://i.imgur.com/RdOsE7s.png",
        "name": "Char Test",
        "nickname": "Char",
        "last_login": "2026-01-01T00:00:00Z",
        "role": "volunteer",
        "github": "chartest",
        "city": "Tempe",
        "want_stickers": "yes",
        "badges": [],
        "teams": [],
    }
    if propel_id:
        doc["propel_id"] = propel_id
    doc.update(extra)
    get_db().collection("users").document(db_id).set(doc)
    return doc


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(ms, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(us, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(us, "get_propel_user_details_by_id", _boom)
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    yield


# ----------------------------- read path ------------------------------------

def test_get_history_old_returns_legacy_key_superset():
    """The frozen legacy read body (uncalled since delegation; deleted in the
    cleanup PR). Kept as the executable definition of LEGACY_PROFILE_KEYS."""
    db_id = "char1111char1111char1111char1111"
    _seed_user(db_id, "oauth2|slack|T123-UCHAR1", "char1@example.com")

    result = ms.get_history_old(db_id)
    assert result is not None
    assert LEGACY_PROFILE_KEYS <= set(result.keys())
    assert result["id"] == db_id


def test_get_profile_metadata_old_keeps_text_envelope_and_keys():
    db_id = "char2222char2222char2222char2222"
    propel_id = "propel-char-2-unique"
    _seed_user(db_id, "oauth2|slack|T123-UCHAR2", "char2@example.com", propel_id=propel_id)

    us.get_profile_metadata.cache_clear()
    response = ms.get_profile_metadata_old(propel_id)
    assert set(response.keys()) == {"text"}, "legacy GET must keep the {'text': ...} envelope"
    assert LEGACY_PROFILE_KEYS <= set(response["text"].keys()), (
        f"delegate lost legacy keys: {LEGACY_PROFILE_KEYS - set(response['text'].keys())}"
    )
    assert response["text"]["id"] == db_id
    assert response["text"]["city"] == "Tempe"
    assert response["text"]["want_stickers"] == "yes"


def test_get_profile_metadata_old_unresolvable_keeps_auth_failed_shape(monkeypatch):
    monkeypatch.setattr(us, "_resolve_and_ensure_user", lambda pid: (None, None))
    us.get_profile_metadata.cache_clear()
    response = ms.get_profile_metadata_old("propel-char-unresolvable-unique")
    assert response == {"error": "Unable to resolve user profile", "status": "auth_failed"}


# ----------------------------- write path -----------------------------------

def test_save_profile_metadata_old_partial_save_does_not_clobber():
    db_id = "char3333char3333char3333char3333"
    propel_id = "propel-char-3-unique"
    _seed_user(db_id, "oauth2|slack|T123-UCHAR3", "char3@example.com", propel_id=propel_id,
               company="Keep Me Inc", github="keepme", bio="keep bio")

    result = ms.save_profile_metadata_old(propel_id, {"metadata": {"city": "Phoenix"}})
    assert result is not None

    saved = get_db().collection("users").document(db_id).get().to_dict()
    assert saved["city"] == "Phoenix"
    # Partial saves must never clobber unrelated fields
    assert saved["company"] == "Keep Me Inc"
    assert saved["github"] == "keepme"
    assert saved["bio"] == "keep bio"
    assert saved["email_address"] == "char3@example.com"


def test_save_profile_metadata_old_unresolvable_user_returns_none(monkeypatch):
    monkeypatch.setattr(us, "_resolve_and_ensure_user", lambda pid: (None, None))
    assert ms.save_profile_metadata_old("propel-char-4-unique", {"metadata": {"city": "X"}}) is None


# ----------------------------- public by-id path ----------------------------

def test_get_user_by_id_old_flat_safe_shape():
    db_id = "char5555char5555char5555char5555"
    _seed_user(db_id, "oauth2|slack|T123-UCHAR5", "char5@example.com")

    result = ms.get_user_by_id_old(db_id)
    for key in ("name", "profile_image", "user_id", "nickname", "id", "github"):
        assert key in result, f"missing {key}"
    assert result["id"] == db_id
    assert result["github"] == "chartest"
    # Never any PII. github IS included here (internal_lookup_fields) — this
    # route backs team rosters/feedback/giveaways, which have always shown a
    # participant's GitHub username regardless of the fully-public-portfolio
    # privacy toggle.
    assert "propel_id" not in result
    assert "email_address" not in result
    assert "profile_slug" in result


def test_get_user_by_id_old_unknown_id_returns_empty():
    assert ms.get_user_by_id_old("nope6666nope6666nope6666nope6666") == {}
