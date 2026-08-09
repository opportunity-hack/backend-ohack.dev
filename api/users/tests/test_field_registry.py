"""The CI guard for the profile field registry.

Adding a profile field = one PROFILE_FIELD_SPECS entry. These tests fail when
any remaining hand-written list (privacy lists, admin lean projection, the
canonical response) drifts out of sync — turning the old "silently dropped
field" bug class into a red build.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from model.user import (
    User,
    PROFILE_FIELD_SPECS,
    OWNER_EDITABLE_FIELDS,
    PROFILE_PERSISTED_FIELDS,
    PROFILE_READONLY_RESPONSE_FIELDS,
    metadata_list,
    privacy_fields,
    pii_fields,
    safe_public_fields,
)

SPEC_NAMES = {n for (n, _d, _e, _p) in PROFILE_FIELD_SPECS}
RESPONSE_NAMES = SPEC_NAMES | set(PROFILE_READONLY_RESPONSE_FIELDS)

# Privacy fields that are DERIVED sections (attached by users_service from
# other collections/computations), not flat storage fields on the user doc.
DERIVED_PRIVACY_FIELDS = {
    "badges", "what", "how", "feedback", "hackathon_history", "praises",
    "teams", "certificates", "github_history", "hearts",
}

# The frozen legacy editor contract (see the retirement plan): the canonical
# response must remain a SUPERSET of what /api/messages/profile always served.
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


def _minimal_user():
    return User.deserialize({"id": "reg11111reg11111reg11111reg11111"})


def test_every_spec_field_is_an_instance_attr_after_deserialize():
    user = _minimal_user()
    for name in SPEC_NAMES:
        assert name in vars(user), f"deserialize must always set {name}"


def test_metadata_list_matches_registry():
    assert set(metadata_list) == SPEC_NAMES


def test_owner_editable_excludes_system_and_dedicated_fields():
    for forbidden in ("propel_id", "volunteering", "bio_video_url", "profile_slug", "profile_visibility"):
        assert forbidden not in OWNER_EDITABLE_FIELDS, (
            f"{forbidden} must never be settable via POST /profile metadata"
        )


def test_volunteering_not_in_generic_write_set():
    # volunteering has a dedicated writer; the generic profile upsert writing
    # it is the lost-update hazard this registry exists to prevent.
    assert "volunteering" not in PROFILE_PERSISTED_FIELDS
    user = _minimal_user()
    assert "volunteering" not in user.serialize_profile_metadata()


def test_update_from_metadata_ignores_non_editable_fields():
    user = _minimal_user()
    original_propel = user.propel_id
    user.update_from_metadata({
        "propel_id": "attacker-propel-id",
        "volunteering": [{"hours": 999}],
        "profile_slug": "stolen-slug",
        "city": "Tempe",
    })
    assert user.propel_id == original_propel
    assert user.volunteering == []
    assert user.profile_slug is None
    assert user.city == "Tempe"


def test_privacy_lists_only_reference_known_fields():
    for field in privacy_fields:
        assert field in SPEC_NAMES | set(PROFILE_READONLY_RESPONSE_FIELDS) | DERIVED_PRIVACY_FIELDS, (
            f"privacy_fields entry {field!r} is neither a registry field nor a known derived section"
        )
    for field in pii_fields:
        assert field in RESPONSE_NAMES, f"pii_fields entry {field!r} unknown"
    for field in safe_public_fields:
        assert field in RESPONSE_NAMES, f"safe_public_fields entry {field!r} unknown"


def test_admin_lean_fields_are_known():
    from api.messages.messages_service import _ADMIN_PROFILE_LEAN_FIELDS
    allowed = RESPONSE_NAMES | {"badges", "teams", "hackathons", "volunteering"}
    for field in _ADMIN_PROFILE_LEAN_FIELDS:
        assert field in allowed, (
            f"_ADMIN_PROFILE_LEAN_FIELDS entry {field!r} is not a known profile field"
        )


def test_canonical_response_is_superset_of_legacy_contract(monkeypatch):
    import services.users_service as us
    monkeypatch.setattr(us, "warning", lambda *a, **k: None)

    user = _minimal_user()
    response = us.build_profile_response(user)
    missing = LEGACY_PROFILE_KEYS - set(response.keys())
    assert not missing, f"canonical response lost legacy keys: {missing}"
    # And the sane defaults hold for a bare doc
    assert response["profile_visibility"] == "private"
    assert response["portfolio_links"] == []
    assert response["badges"] == []
    assert isinstance(response["history"], dict)
