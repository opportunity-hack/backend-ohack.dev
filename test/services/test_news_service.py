import sys
from unittest.mock import MagicMock, patch

_mock_modules = [
    "db.db", "db.firestore", "db.mem", "db.interface",
    "google.cloud", "google.cloud.storage", "google.cloud.firestore",
    "common.utils.firebase", "common.utils.slack", "common.utils.cdn",
    "common.utils.openai_api", "common.utils.firestore_helpers", "openai",
]
for mod_name in _mock_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

from services import news_service  # noqa: E402

BASE = {
    "title": "T", "description": "D", "slack_ts": "1.0",
    "slack_permalink": "p", "slack_channel": "c",
}


def _save(existing_image=None, gen=None):
    upsert = MagicMock(return_value="id1")
    with patch.object(news_service, "get_news_image_by_slack_ts", return_value=existing_image), \
         patch.object(news_service, "generate_and_save_image_to_cdn", gen or MagicMock(return_value="img.png")) as g, \
         patch.object(news_service, "upsert_news", upsert), \
         patch.object(news_service, "CDN_SERVER", "https://cdn"):
        news_service.save_news(dict(BASE))
    return upsert.call_args[0][0], g


def test_resave_keeps_existing_image_and_skips_generation():
    saved, gen = _save(existing_image="https://cdn/old.png")
    assert saved["image"] == "https://cdn/old.png"
    gen.assert_not_called()


def test_new_news_generates_image():
    saved, gen = _save()
    assert saved["image"] == "https://cdn/ohack.dev/news/img.png"
    gen.assert_called_once()


def test_failed_generation_does_not_write_image_key():
    saved, _ = _save(gen=MagicMock(side_effect=Exception("401")))
    assert "image" not in saved
