import pytest

from common.utils.validators import validate_social_posts


def test_validate_social_posts_allows_article_platform_urls():
    validate_social_posts([
        {"platform": "article", "url": "https://example.com/story"},
    ])


def test_validate_social_posts_rejects_invalid_article_url():
    with pytest.raises(ValueError, match=r"social_posts\[0\]\.url must be a valid URL"):
        validate_social_posts([
            {"platform": "article", "url": "not-a-url"},
        ])


def test_validate_social_posts_keeps_platform_host_checks():
    with pytest.raises(ValueError, match=r"social_posts\[0\]\.url host must match platform 'linkedin'"):
        validate_social_posts([
            {"platform": "linkedin", "url": "https://example.com/story"},
        ])

from common.utils.validators import (
    ALLOWED_MEALS_MODES,
    MAX_MEALS_NOTE_LENGTH,
    validate_hackathon_data,
    validate_hackathon_data_partial,
    validate_meals,
)


def _hackathon_data(constraints_extra=None):
    """Minimal payload that passes both hackathon validators."""
    constraints = {
        "max_people_per_team": 5,
        "max_teams_per_problem": 3,
        "min_people_per_team": 2,
    }
    constraints.update(constraints_extra or {})
    return {
        "title": "Test Hackathon",
        "description": "A test",
        "location": "Tempe, Arizona",
        "start_date": "2026-10-10",
        "end_date": "2026-10-12",
        "type": "hackathon",
        "image_url": "https://cdn.ohack.dev/test.webp",
        "event_id": "fall-2026-test",
        "constraints": constraints,
    }


def test_validate_meals_allows_meal_without_items():
    # A times-only ("schedule" mode) slot carries no menu items at all.
    validate_meals([
        {"id": "m1", "name": "Saturday Lunch", "time": "2026-10-10T12:00:00Z"},
        {"id": "m2", "name": "Saturday Dinner", "items": []},
    ])


def test_validate_hackathon_data_accepts_meals_modes():
    for mode in sorted(ALLOWED_MEALS_MODES):
        validate_hackathon_data(_hackathon_data({"meals_mode": mode}))
    # Unset / empty behave as the default menu mode
    validate_hackathon_data(_hackathon_data({"meals_mode": None}))
    validate_hackathon_data(_hackathon_data({"meals_mode": ""}))
    validate_hackathon_data(_hackathon_data())


def test_validate_hackathon_data_rejects_bad_meals_mode():
    with pytest.raises(ValueError, match="meals_mode must be one of"):
        validate_hackathon_data(_hackathon_data({"meals_mode": "buffet"}))
    with pytest.raises(ValueError, match="meals_mode must be one of"):
        validate_hackathon_data(_hackathon_data({"meals_mode": ["schedule"]}))


def test_validate_hackathon_data_meals_note_bounds():
    validate_hackathon_data(
        _hackathon_data({"meals_note": "Breakfast, lunch, and dinner provided."})
    )
    with pytest.raises(ValueError, match="meals_note must be a string"):
        validate_hackathon_data(
            _hackathon_data({"meals_note": "x" * (MAX_MEALS_NOTE_LENGTH + 1)})
        )
    with pytest.raises(ValueError, match="meals_note must be a string"):
        validate_hackathon_data(_hackathon_data({"meals_note": 42}))


def test_partial_keeps_valid_meals_mode_and_note():
    cleaned, skipped = validate_hackathon_data_partial(
        _hackathon_data({"meals_mode": "schedule", "meals_note": "Meals provided."})
    )
    assert skipped == []
    assert cleaned["constraints"]["meals_mode"] == "schedule"
    assert cleaned["constraints"]["meals_note"] == "Meals provided."


def test_partial_strips_invalid_meals_mode_but_saves_rest():
    cleaned, skipped = validate_hackathon_data_partial(
        _hackathon_data({"meals_mode": "buffet", "meals_note": "Meals provided."})
    )
    assert any(s["field"] == "constraints.meals_mode" for s in skipped)
    assert "meals_mode" not in cleaned["constraints"]
    assert cleaned["constraints"]["meals_note"] == "Meals provided."


def test_partial_strips_invalid_meals_note():
    cleaned, skipped = validate_hackathon_data_partial(
        _hackathon_data({"meals_note": "x" * (MAX_MEALS_NOTE_LENGTH + 1)})
    )
    assert any(s["field"] == "constraints.meals_note" for s in skipped)
    assert "meals_note" not in cleaned["constraints"]


# ---------------------------------------------------------------------------
# validate_volunteer_admin_patch — the generic hackathon PATCH must never be a
# side door for the roster bit; status is folded onto the catalog.
# ---------------------------------------------------------------------------
from common.utils.validators import (  # noqa: E402
    ALLOWED_VOLUNTEER_STATUSES,
    validate_volunteer_admin_patch,
)


def test_volunteer_admin_patch_strips_isSelected_and_type():
    cleaned = validate_volunteer_admin_patch(
        {"id": "v1", "name": "Jane", "isSelected": True, "type": "judges", "status": "approved"}
    )
    assert cleaned == {"id": "v1", "name": "Jane", "status": "approved"}


def test_volunteer_admin_patch_does_not_mutate_input():
    payload = {"id": "v1", "isSelected": True}
    validate_volunteer_admin_patch(payload)
    assert payload == {"id": "v1", "isSelected": True}


def test_volunteer_admin_patch_folds_status_case_and_blank():
    assert validate_volunteer_admin_patch({"id": "v1", "status": " Approved "})["status"] == "approved"
    assert validate_volunteer_admin_patch({"id": "v1", "status": "Verified Travel"})["status"] == "verified_travel"
    assert validate_volunteer_admin_patch({"id": "v1", "status": ""})["status"] == "pending"
    assert validate_volunteer_admin_patch({"id": "v1", "status": None})["status"] == "pending"


def test_volunteer_admin_patch_rejects_unknown_status():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        validate_volunteer_admin_patch({"id": "v1", "status": "selected"})
    assert "waitlisted" in ALLOWED_VOLUNTEER_STATUSES


def test_volunteer_admin_patch_passes_other_fields_through():
    cleaned = validate_volunteer_admin_patch({"id": "v1", "biography": "x", "checkedIn": True})
    assert cleaned == {"id": "v1", "biography": "x", "checkedIn": True}
