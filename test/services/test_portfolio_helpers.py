"""Pure-function tests: hearts summary/tiers + certificate github_username extraction."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from services.hearts_service import get_hearts_summary, get_heart_tier


def test_hearts_summary_sums_what_and_how_only():
    history = {
        "what": {"code_quality": 1.5, "documentation": 0.5, "judge": 1},
        "how": {"standups_completed": 2},
        "certificates": [{"filename": "x.png", "hearts": 99}],  # must be ignored
        "unrelated_future_key": {"foo": 100},                    # must be ignored
    }
    summary = get_hearts_summary(history)
    assert summary["total"] == 5.0
    assert summary["breakdown"]["what"]["code_quality"] == 1.5
    assert summary["tier"] == "Silver"


def test_hearts_summary_handles_missing_and_malformed():
    assert get_hearts_summary(None)["total"] == 0
    assert get_hearts_summary({})["tier"] is None
    summary = get_hearts_summary({"what": {"a": "not-a-number", "b": 2}, "how": "bogus"})
    assert summary["total"] == 2.0


def test_heart_tiers():
    assert get_heart_tier(0) is None
    assert get_heart_tier(2) == "Bronze"
    assert get_heart_tier(5) == "Silver"
    assert get_heart_tier(10) == "Gold"
    assert get_heart_tier(24) == "Platinum"
    assert get_heart_tier(48) == "Diamond"
    assert get_heart_tier(100) == "Diamond"
    assert get_heart_tier("nan-ish") is None


def test_extract_github_username():
    from api.certificates.certificate_service import _extract_github_username

    # Standard noreply encoding
    assert _extract_github_username(
        "123714233+aitzeng@users.noreply.github.com", "Anthony Tzeng") == "aitzeng"
    # Old-style noreply (no numeric id)
    assert _extract_github_username(
        "someuser@users.noreply.github.com", None) == "someuser"
    # Fallback: author_name that looks like a login
    assert _extract_github_username("real@example.com", "gregv") == "gregv"
    # Full name is not a login; unrelated email -> None
    assert _extract_github_username("real@example.com", "Greg V") is None
    assert _extract_github_username(None, None) is None
    # Case is normalized
    assert _extract_github_username("1+AiTzEnG@users.noreply.github.com", None) == "aitzeng"


def test_certificates_attach_without_privacy_opt_in():
    """Certificates have no privacy toggle — they always ride the public profile."""
    from types import SimpleNamespace
    from services.users_service import _attach_certificates

    user = SimpleNamespace(
        history={"certificates": [
            "old.png",
            {"filename": "new.png", "reasons": ["mentor"], "hearts": 1, "event_id": "fall-2026"},
        ]},
        github="",
    )
    public_data = {}
    _attach_certificates(user, public_data, {})  # no "certificates": "public"

    urls = [c["url"] for c in public_data["certificates"]["heart_certificates"]]
    assert [u.rsplit("/", 1)[-1] for u in urls] == ["old.png", "new.png"]
