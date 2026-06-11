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