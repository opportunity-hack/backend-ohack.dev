"""Tests for the public-portfolio backend: slug claims, visibility gating,
and the privacy matrix of get_public_profile_data.

ENVIRONMENT=test -> MockFirestore; no network at import.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.users_service as us
import services.user_slug_service as slugs
from model.user import User, DEFAULT_PROFILE_VISIBILITY


def _make_user(db_id, slug=None):
    user = User()
    user.id = db_id
    user.name = "Test User"
    user.nickname = "Tester"
    user.profile_image = "https://i.imgur.com/RdOsE7s.png"
    user.user_id = f"oauth2|slack|T123-{db_id}"
    user.profile_slug = slug
    return user


def _patch_resolve(monkeypatch, user):
    monkeypatch.setattr(us, "_resolve_and_ensure_user", lambda _pid: (user, user.user_id))


# ----------------------------- slug validation -----------------------------

@pytest.mark.parametrize("slug,valid", [
    ("gregv", True),
    ("greg-v", True),
    ("a1b2c3", True),
    ("ab", False),                      # too short
    ("a" * 31, False),                  # too long
    ("-greg", False),                   # leading hyphen
    ("greg-", False),                   # trailing hyphen
    ("Greg", False),                    # normalize first — validate_slug expects lowercase
    ("greg v", False),                  # space
    ("admin", False),                   # reserved
    ("profile", False),                 # reserved
    ("u", False),                       # reserved + short
    ("0123456789abcdef0123456789abcdef", False),  # 32-hex — could shadow a db id
])
def test_validate_slug(slug, valid):
    ok, _reason = slugs.validate_slug(slug)
    assert ok is valid


def test_normalize_slug():
    assert slugs.normalize_slug("  GregV ") == "gregv"


# ----------------------------- claim + conflict -----------------------------

def test_claim_and_conflict(monkeypatch):
    alice = _make_user("aaaa1111aaaa1111aaaa1111aaaa1111")
    _patch_resolve(monkeypatch, alice)

    payload, status = slugs.claim_profile_slug("propel-alice", "alice-portfolio")
    assert status == 200
    assert payload["slug"] == "alice-portfolio"
    assert payload["previous_slug"] is None

    # A different user cannot take it
    bob = _make_user("bbbb2222bbbb2222bbbb2222bbbb2222")
    _patch_resolve(monkeypatch, bob)
    payload, status = slugs.claim_profile_slug("propel-bob", "Alice-Portfolio")
    assert status == 409

    # The pointer resolves to alice
    from db.db import fetch_user_db_id_by_slug
    pointer = fetch_user_db_id_by_slug("alice-portfolio")
    assert pointer["user_db_id"] == alice.id
    assert pointer["is_primary"] is True


def test_claim_same_slug_is_idempotent(monkeypatch):
    carol = _make_user("cccc3333cccc3333cccc3333cccc3333")
    _patch_resolve(monkeypatch, carol)

    payload, status = slugs.claim_profile_slug("propel-carol", "carol")
    assert status == 200
    payload, status = slugs.claim_profile_slug("propel-carol", "carol")
    assert status == 200
    assert payload.get("previous_slug") is None


def test_rename_is_throttled(monkeypatch):
    dave = _make_user("dddd4444dddd4444dddd4444dddd4444")
    _patch_resolve(monkeypatch, dave)

    payload, status = slugs.claim_profile_slug("propel-dave", "dave-one")
    assert status == 200
    # Immediate rename hits the 24h cooldown
    payload, status = slugs.claim_profile_slug("propel-dave", "dave-two")
    assert status == 429


def test_invalid_slug_rejected(monkeypatch):
    erin = _make_user("eeee5555eeee5555eeee5555eeee5555")
    _patch_resolve(monkeypatch, erin)
    _payload, status = slugs.claim_profile_slug("propel-erin", "admin")
    assert status == 400


# ----------------------------- visibility gating ----------------------------

def test_public_visibility_requires_slug(monkeypatch):
    frank = _make_user("ffff6666ffff6666ffff6666ffff6666", slug=None)
    _patch_resolve(monkeypatch, frank)

    payload, status = us.set_profile_visibility("propel-frank", "public")
    assert status == 400

    payload, status = us.set_profile_visibility("propel-frank", "bogus")
    assert status == 400

    frank.profile_slug = "frank"
    payload, status = us.set_profile_visibility("propel-frank", "public")
    assert status == 200
    assert payload["profile_visibility"] == "public"

    payload, status = us.set_profile_visibility("propel-frank", "private")
    assert status == 200


# ----------------------------- privacy matrix -------------------------------

def test_public_profile_defaults_are_private():
    user = _make_user("9999aaaa9999aaaa9999aaaa9999aaaa", slug="niner")
    user.github = "someuser"
    user.bio = "My bio"
    user.headline = "Builder"
    user.portfolio_links = [{"label": "Site", "url": "https://example.com"}]
    user.history = {"what": {"code_quality": 2}, "how": {"standups_completed": 1}}

    data = user.get_public_profile_data()

    # Safe fields always present
    assert data["name"] == "Test User"
    assert data["id"] == user.id
    assert data["profile_slug"] == "niner"
    # Master toggle defaults private
    assert data["profile_visibility"] == DEFAULT_PROFILE_VISIBILITY == "private"
    # Privacy-gated fields absent by default
    for hidden in ("github", "bio", "headline", "portfolio_links", "history",
                   "teams", "certificates", "github_history", "hearts"):
        assert hidden not in data, f"{hidden} leaked with default privacy"


def test_public_profile_opt_ins():
    user = _make_user("8888bbbb8888bbbb8888bbbb8888bbbb")
    user.bio = "My bio"
    user.headline = "Builder"
    user.portfolio_links = [{"label": "Site", "url": "https://example.com"}]
    user.privacy_settings = {"bio": "public", "portfolio_links": "public"}

    data = user.get_public_profile_data()
    assert data["bio"] == "My bio"
    assert data["headline"] == "Builder"  # rides the bio toggle
    assert data["portfolio_links"] == [{"label": "Site", "url": "https://example.com"}]


def test_headline_hidden_when_bio_private():
    user = _make_user("7777cccc7777cccc7777cccc7777cccc")
    user.headline = "Builder"
    user.privacy_settings = {"bio": "private"}
    data = user.get_public_profile_data()
    assert "headline" not in data


def test_get_profile_by_db_id_never_leaks_propel_id_but_includes_github(monkeypatch):
    user = _make_user("6666dddd6666dddd6666dddd6666dddd")
    user.propel_id = "propel-secret"
    user.github = "somegithub"
    monkeypatch.setattr(us, "get_user_profile_by_db_id", lambda _id: user)

    result = us.get_profile_by_db_id(user.id)
    assert "propel_id" not in result
    # github is part of internal_lookup_fields (not safe_public_fields) —
    # this internal by-id route backs team rosters/feedback/giveaways, which
    # have always shown a participant's GitHub username regardless of the
    # fully-public-portfolio privacy toggle tested above.
    assert result["github"] == "somegithub"
    assert result["name"] == "Test User"


# ----------------------------- metadata sanitization ------------------------

def test_sanitize_portfolio_metadata():
    metadata = {
        "bio": "  x" + "y" * 3000,
        "headline": "h" * 200,
        "portfolio_links": [
            {"label": "ok", "url": "example.com/portfolio"},
            {"label": "bad", "url": "not a url at all"},
            "not-a-dict",
            {"label": "l" * 100, "url": "https://good.example.org"},
        ],
    }
    us._sanitize_portfolio_metadata(metadata)
    assert len(metadata["bio"]) <= us.MAX_BIO_LENGTH
    assert len(metadata["headline"]) == us.MAX_HEADLINE_LENGTH
    urls = [l["url"] for l in metadata["portfolio_links"]]
    assert "https://example.com/portfolio" in urls  # https auto-prefixed
    assert all(u.startswith("http") for u in urls)
    assert len(metadata["portfolio_links"]) == 2  # invalid entries dropped
    assert all(len(l["label"]) <= us.MAX_LINK_LABEL_LENGTH for l in metadata["portfolio_links"])
