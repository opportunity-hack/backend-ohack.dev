"""Tests for the praise-bot config service (praise_bot_config collection)."""
import pytest
from unittest.mock import patch
from mockfirestore import MockFirestore

from api.praisebot import praisebot_service as svc


ACTOR = {"propel_user_id": "test-user", "email": "greg@ohack.org"}


@pytest.fixture
def db():
    mock_db = MockFirestore()
    svc._clear_cache()
    with patch.object(svc, "get_db", return_value=mock_db):
        yield mock_db
    svc._clear_cache()


def _valid_watcher(**overrides):
    doc = {
        "type": "github_watcher",
        "name": "Core repos",
        "enabled": True,
        "source": {
            "mode": "repos",
            "repos": ["opportunity-hack/frontend-ohack.dev",
                      "https://github.com/opportunity-hack/backend-ohack.dev"],
            "channels": "ohack-dev, #general",
        },
        "digest": {"enabled": True, "cron": "0 16 * * *"},
        "rollup": {"enabled": False},
    }
    doc.update(overrides)
    return doc


class TestGetFullConfig:
    def test_empty_collection_is_unconfigured(self, db):
        cfg = svc.get_full_config()
        assert cfg["configured"] is False
        assert cfg["github_watchers"] == []
        assert cfg["calendar_reminders"] == []
        assert cfg["community"] is None
        assert cfg["global"] == {"dry_run": False, "llm_enabled": True}

    def test_assembles_docs_by_type_and_strips_audit(self, db):
        svc.create_config_doc(_valid_watcher(), ACTOR)
        svc.update_config_doc("global", {"dry_run": True}, ACTOR)
        svc._clear_cache()

        cfg = svc.get_full_config()
        assert cfg["configured"] is True
        assert cfg["global"]["dry_run"] is True
        assert len(cfg["github_watchers"]) == 1
        watcher = cfg["github_watchers"][0]
        assert "updated_by" not in watcher
        assert "id" in watcher

        admin_cfg = svc.get_full_config(include_audit=True)
        assert admin_cfg["github_watchers"][0]["updated_by"] == ACTOR


class TestCreate:
    def test_create_watcher_normalizes_repos_and_channels(self, db):
        body, status = svc.create_config_doc(_valid_watcher(), ACTOR)
        assert status == 201
        stored = db.collection(svc.COLLECTION).document(body["id"]).get().to_dict()
        assert stored["source"]["repos"] == [
            "opportunity-hack/frontend-ohack.dev",
            "opportunity-hack/backend-ohack.dev",
        ]
        assert stored["source"]["channels"] == ["ohack-dev", "general"]

    def test_create_rejects_bad_cron(self, db):
        bad = _valid_watcher(digest={"enabled": True, "cron": "every day at 9"})
        body, status = svc.create_config_doc(bad, ACTOR)
        assert status == 400
        assert "cron" in body["error"]

    def test_create_rejects_unknown_type_and_global(self, db):
        assert svc.create_config_doc({"type": "nope"}, ACTOR)[1] == 400
        assert svc.create_config_doc({"type": "global"}, ACTOR)[1] == 400

    def test_hackathon_mode_requires_event_id(self, db):
        doc = _valid_watcher(source={"mode": "hackathon"})
        body, status = svc.create_config_doc(doc, ACTOR)
        assert status == 400
        assert "event_id" in body["error"]

    def test_rollup_enabled_requires_channel(self, db):
        doc = _valid_watcher(rollup={"enabled": True, "cron": "0 14 * * 1"})
        body, status = svc.create_config_doc(doc, ACTOR)
        assert status == 400
        assert "rollup.channel" in body["error"]

    def test_secret_looking_keys_are_dropped(self, db):
        doc = _valid_watcher(github_token="sekret", api_key="sekret")
        body, status = svc.create_config_doc(doc, ACTOR)
        assert status == 201
        stored = db.collection(svc.COLLECTION).document(body["id"]).get().to_dict()
        assert "github_token" not in stored
        assert "api_key" not in stored

    def test_calendar_reminder_bounds(self, db):
        base = {
            "type": "calendar_reminder", "name": "Office hours", "enabled": True,
            "calendar_id": "abc@group.calendar.google.com",
            "channels": ["general"], "lead_minutes": 15,
            "poll_cron": "*/5 * * * *",
        }
        assert svc.create_config_doc(base, ACTOR)[1] == 201
        assert svc.create_config_doc({**base, "lead_minutes": 0}, ACTOR)[1] == 400
        assert svc.create_config_doc({**base, "lead_minutes": 500}, ACTOR)[1] == 400

    def test_calendar_id_normalizes_share_links(self, db):
        real_id = ("c_15c6f25ddc611081a1c59ef917c647fb48a58ae716916c5792"
                   "eede6a2236ed10@group.calendar.google.com")
        import base64
        cid = base64.b64encode(real_id.encode()).decode().rstrip("=")
        base = {
            "type": "calendar_reminder", "name": "Office hours", "enabled": True,
            "channels": ["general"], "lead_minutes": 15, "poll_cron": "*/5 * * * *",
        }
        cases = [
            f"https://calendar.google.com/calendar/u/0?cid={cid}",
            f"https://calendar.google.com/calendar/embed?src={real_id}",
            f"https://calendar.google.com/calendar/ical/{real_id.replace('@', '%40')}/public/basic.ics",
            real_id.replace("@", "%40"),
            real_id,
        ]
        for pasted in cases:
            body, status = svc.create_config_doc({**base, "calendar_id": pasted}, ACTOR)
            assert status == 201, f"failed for {pasted}: {body}"
            stored = db.collection(svc.COLLECTION).document(body["id"]).get().to_dict()
            assert stored["calendar_id"] == real_id, f"not normalized for {pasted}"

        body, status = svc.create_config_doc({**base, "calendar_id": "not-a-calendar"}, ACTOR)
        assert status == 400
        assert "calendar_id" in body["error"]

    def test_community_is_singleton(self, db):
        community = {
            "type": "community", "enabled": True, "intro_channel": "introductions",
            "matchmaker": {"enabled": True, "max_matches": 3},
            "digest": {"enabled": True, "cron": "0 17 * * 1", "channel": "general"},
        }
        assert svc.create_config_doc(community, ACTOR)[1] == 201
        body, status = svc.create_config_doc(community, ACTOR)
        assert status == 400
        assert "already exists" in body["error"]


class TestUpdateDelete:
    def test_global_upsert_and_delete_refused(self, db):
        body, status = svc.update_config_doc("global", {"llm_enabled": False}, ACTOR)
        assert status == 200
        svc._clear_cache()
        assert svc.get_full_config()["global"]["llm_enabled"] is False
        assert svc.delete_config_doc("global")[1] == 400

    def test_update_validates_against_stored_type(self, db):
        doc_id = svc.create_config_doc(_valid_watcher(), ACTOR)[0]["id"]
        body, status = svc.update_config_doc(
            doc_id, {"digest": {"enabled": True, "cron": "bad"}}, ACTOR)
        assert status == 400

        body, status = svc.update_config_doc(doc_id, {"enabled": False}, ACTOR)
        assert status == 200
        stored = db.collection(svc.COLLECTION).document(doc_id).get().to_dict()
        assert stored["enabled"] is False
        assert stored["updated_by"] == ACTOR

    def test_update_and_delete_missing_doc_404(self, db):
        assert svc.update_config_doc("nope", {"enabled": True}, ACTOR)[1] == 404
        assert svc.delete_config_doc("nope")[1] == 404

    def test_delete_watcher(self, db):
        doc_id = svc.create_config_doc(_valid_watcher(), ACTOR)[0]["id"]
        assert svc.delete_config_doc(doc_id)[1] == 200
        svc._clear_cache()
        assert svc.get_full_config()["configured"] is False


class TestApiKeyHelper:
    def test_check_api_key(self, monkeypatch):
        from common.utils.api_key import check_api_key

        class FakeRequest:
            def __init__(self, key):
                self.headers = {"X-Api-Key": key} if key else {}

        monkeypatch.delenv("BACKEND_BOT_CONFIG_TOKEN", raising=False)
        monkeypatch.delenv("BACKEND_PRAISE_TOKEN", raising=False)
        # No env configured -> fail closed
        assert not check_api_key(FakeRequest("x"), "BACKEND_BOT_CONFIG_TOKEN", "BACKEND_PRAISE_TOKEN")

        monkeypatch.setenv("BACKEND_PRAISE_TOKEN", "fallback")
        assert check_api_key(FakeRequest("fallback"), "BACKEND_BOT_CONFIG_TOKEN", "BACKEND_PRAISE_TOKEN")

        # Dedicated token takes precedence over fallback
        monkeypatch.setenv("BACKEND_BOT_CONFIG_TOKEN", "primary")
        assert check_api_key(FakeRequest("primary"), "BACKEND_BOT_CONFIG_TOKEN", "BACKEND_PRAISE_TOKEN")
        assert not check_api_key(FakeRequest("fallback"), "BACKEND_BOT_CONFIG_TOKEN", "BACKEND_PRAISE_TOKEN")
        assert not check_api_key(FakeRequest(None), "BACKEND_BOT_CONFIG_TOKEN")
