"""
Tests for dynamic per-team LiteLLM gateway keys (api/teams/gateway_keys.py)
and the view-layer wiring in api/teams/teams_views.py.

Service tests use a fake Firestore doc store and a stubbed requests.post;
no real Firestore or LiteLLM is touched. The view tests reuse the
propelauth-stub pattern from test_teams_devpost_demo_video_views.py.
"""
import functools
import importlib
import json
import os
import re
import sys
import types
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from flask import Flask, g
from werkzeug.local import LocalProxy

os.environ.setdefault("ENVIRONMENT", "test")

VIEWS_MODULE = "api.teams.teams_views"
SERVICE_MODULE = "api.teams.gateway_keys"
FAKE_USER = types.SimpleNamespace(user_id="caller-propel-uuid", email="caller@example.com")


# ---------------------------------------------------------------------------
# Fake Firestore
# ---------------------------------------------------------------------------

class FakeSnap:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data else None


class FakeDoc:
    def __init__(self, store, doc_id):
        self._store = store
        self._id = doc_id

    def get(self):
        return FakeSnap(self._store.get(self._id))

    def set(self, data, merge=False):
        if merge:
            current = dict(self._store.get(self._id) or {})
            current.update(data)
            self._store[self._id] = current
        else:
            self._store[self._id] = dict(data)

    def delete(self):
        self._store.pop(self._id, None)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        store = self.collections.setdefault(name, {})

        class _C:
            def document(_, doc_id):
                return FakeDoc(store, doc_id)

        return _C()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "test-master-key")
    monkeypatch.setenv("GATEWAY_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LITELLM_BASE_URL", "https://gateway.example.test")
    sys.modules.pop(SERVICE_MODULE, None)
    module = importlib.import_module(SERVICE_MODULE)
    fake_db = FakeDB()
    monkeypatch.setattr(module, "get_db", lambda: fake_db)
    yield module, fake_db
    sys.modules.pop(SERVICE_MODULE, None)


def _ok_generate(key="sk-team-plain-1"):
    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/key/generate")
        assert headers["Authorization"] == "Bearer test-master-key"
        resp = MagicMock()
        resp.json.return_value = {"key": key}
        resp.raise_for_status.return_value = None
        fake_post.calls.append(json)
        return resp

    fake_post.calls = []
    return fake_post


def _alias_generate():
    """Stub /key/generate returning a distinct key per alias, so Key 1 and
    Key 2 plaintexts can be told apart in assertions."""
    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/key/generate")
        assert headers["Authorization"] == "Bearer test-master-key"
        key = (
            "sk-key2-plain"
            if json["key_alias"].endswith("-frontier")
            else "sk-key1-plain"
        )
        resp = MagicMock()
        resp.json.return_value = {"key": key}
        resp.raise_for_status.return_value = None
        fake_post.calls.append(json)
        return resp

    fake_post.calls = []
    return fake_post


def _fernet_for_test():
    return Fernet(os.environ["GATEWAY_KEY_ENCRYPTION_KEY"])


def _seed_key1_only(module, fake_db, team_id="team-1"):
    """A doc with only Key 1 active, as if provisioned before Key 2 existed."""
    fake_db.collections.setdefault("team_gateway_keys", {})[team_id] = {
        "team_id": team_id,
        "key_alias": "fall26-team-1",
        "status": "active",
        "key_ciphertext": _fernet_for_test().encrypt(b"sk-key1-plain").decode(),
        "models": ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"],
        "max_budget": 15.0,
        # Minted before Nov 10 2026, when the expiry was midnight AZ.
        "expires": "2026-11-16T07:00:00Z",
        "provisioned_at": "2026-11-07T00:00:00+00:00",
    }


def _seed_key2_only(module, fake_db, team_id="team-1"):
    """A doc with only Key 2 active (Key 1 lost, e.g. a failed rotation)."""
    fake_db.collections.setdefault("team_gateway_keys", {})[team_id] = {
        "team_id": team_id,
        "key_alias": "fall26-team-1",
        "status": "missing",
        "key2_status": "active",
        "key2_ciphertext": _fernet_for_test().encrypt(b"sk-key2-plain").decode(),
        "key2_alias": "fall26-team-1-frontier",
        "key2_models": ["muse-wonder", "kimi-k3"],
        "key2_max_budget": 20.0,
        "key2_provisioned_at": "2026-11-07T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _key_duration
# ---------------------------------------------------------------------------

def test_key_duration_is_seconds_until_expiry(service):
    module, _ = service
    from datetime import datetime, timezone

    now = datetime(2026, 11, 13, 1, 0, 0, tzinfo=timezone.utc)
    # 3 days before 2026-11-16T01:00:00Z → exactly 259200 seconds.
    assert module._key_duration(now=now) == "259200s"


def test_expiry_is_sunday_6pm_arizona(service):
    module, _ = service
    from datetime import datetime, timedelta, timezone

    until = datetime.fromisoformat(module.GATEWAY_KEY_EXPIRES.replace("Z", "+00:00"))
    arizona = until.astimezone(timezone(timedelta(hours=-7)))
    assert (arizona.year, arizona.month, arizona.day, arizona.hour, arizona.minute) == (
        2026, 11, 15, 18, 0,
    )
    assert arizona.strftime("%A") == "Sunday"


def test_effective_expires_reports_the_earlier_instant(service):
    module, _ = service
    # Stored before the expiry moved earlier → the new (earlier) constant.
    assert module._effective_expires("2026-11-16T07:00:00Z") == "2026-11-16T01:00:00Z"
    # A stored value already at/before the constant is kept as-is.
    assert module._effective_expires("2026-11-16T01:00:00Z") == "2026-11-16T01:00:00Z"
    assert module._effective_expires("2026-11-15T20:00:00Z") == "2026-11-15T20:00:00Z"
    # Missing or garbage → the constant.
    assert module._effective_expires(None) == "2026-11-16T01:00:00Z"
    assert module._effective_expires("not-a-date") == "2026-11-16T01:00:00Z"


def test_old_stamp_reported_as_new_expiry_in_member_and_admin_reads(service, monkeypatch):
    module, fake_db = service
    _seed_key1_only(module, fake_db)
    fake_db.collections.setdefault("teams", {})["team-1"] = {"name": "T"}
    monkeypatch.setattr(module, "user_is_on_team", lambda *_: True)
    monkeypatch.setattr(module, "_key_spend_best_effort", lambda *_: 1.0)

    payload, status = module.get_team_gateway_key("u", "team-1")
    assert status == 200
    assert {k["expires"] for k in payload["keys"]} == {"2026-11-16T01:00:00Z"}

    batch = module.list_gateway_key_statuses(["team-1"])
    assert {k["expires"] for k in batch["team-1"]["keys"]} == {"2026-11-16T01:00:00Z"}
    # Read-only: the stored stamp is untouched.
    assert fake_db.collections["team_gateway_keys"]["team-1"]["expires"] == "2026-11-16T07:00:00Z"


def test_key_duration_refuses_past_expiry(service):
    module, _ = service
    from datetime import datetime, timezone

    now = datetime(2026, 11, 16, 1, 0, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="in the past"):
        module._key_duration(now=now)


# ---------------------------------------------------------------------------
# provision_team_gateway_key
# ---------------------------------------------------------------------------

def test_provision_mints_both_keys_with_expected_bodies(service, monkeypatch):
    module, fake_db = service
    fake_post = _alias_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.provision_team_gateway_key("team-1")

    assert set(meta) == {"team_id", "keys"}
    assert meta["team_id"] == "team-1"
    assert [k["label"] for k in meta["keys"]] == ["Key 1", "Key 2"]
    key1_meta, key2_meta = meta["keys"]
    assert key1_meta == {
        "label": "Key 1",
        "key_alias": "fall26-team-1",
        "models": ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"],
        "max_budget": 15.0,
        "expires": "2026-11-16T01:00:00Z",
        "status": "active",
    }
    assert key2_meta == {
        "label": "Key 2",
        "key_alias": "fall26-team-1-frontier",
        "models": ["muse-wonder", "kimi-k3"],
        "max_budget": 20.0,
        "expires": "2026-11-16T01:00:00Z",
        "status": "active",
    }
    assert len(fake_post.calls) == 2
    key1_body, key2_body = fake_post.calls
    assert key1_body["key_alias"] == "fall26-team-1"
    assert key1_body["models"] == ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"]
    assert key1_body["max_budget"] == 15.0
    assert "budget_duration" not in key1_body  # lifetime cap, never resets
    # LiteLLM has no `expires` on /key/generate (silently dropped — keys never
    # expired); expiry travels as a `duration` in seconds until GATEWAY_KEY_EXPIRES.
    assert "expires" not in key1_body
    assert re.fullmatch(r"\d+s", key1_body["duration"])
    assert key2_body["key_alias"] == "fall26-team-1-frontier"
    assert key2_body["models"] == ["muse-wonder", "kimi-k3"]
    assert key2_body["max_budget"] == 20.0
    assert "budget_duration" not in key2_body
    assert "expires" not in key2_body
    assert key2_body["duration"] == key1_body["duration"]
    assert key2_body["metadata"]["ohack_team_id"] == "team-1"
    assert key2_body["metadata"]["event"] == "fall-2026"
    assert key2_body["metadata"]["provisioned_by"] == "ohack-backend"
    assert key2_body["metadata"]["key_label"] == "key2"

    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert doc["status"] == "active"
    assert doc["key2_status"] == "active"
    assert doc["key2_alias"] == "fall26-team-1-frontier"
    assert doc["key2_models"] == ["muse-wonder", "kimi-k3"]
    assert doc["key2_max_budget"] == 20.0
    assert "sk-key1-plain" not in json.dumps(doc)  # plaintext never stored raw
    assert "sk-key2-plain" not in json.dumps(doc)
    fernet = _fernet_for_test()
    assert fernet.decrypt(doc["key_ciphertext"].encode()).decode() == "sk-key1-plain"
    assert fernet.decrypt(doc["key2_ciphertext"].encode()).decode() == "sk-key2-plain"


def test_provision_is_idempotent(service, monkeypatch):
    module, fake_db = service
    fake_post = _alias_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    first = module.provision_team_gateway_key("team-1")
    second = module.provision_team_gateway_key("team-1")

    assert len(fake_post.calls) == 2  # second call minted nothing
    assert first == second


def test_provision_cleans_pending_doc_on_failure(service, monkeypatch):
    module, fake_db = service

    def boom(url, json=None, headers=None, timeout=None):
        raise RuntimeError("LiteLLM down")

    monkeypatch.setattr(module.requests, "post", boom)

    with pytest.raises(RuntimeError):
        module.provision_team_gateway_key("team-1")

    assert "team-1" not in fake_db.collections["team_gateway_keys"]


def test_provision_key2_failure_keeps_key1_and_retry_provisions_key2(service, monkeypatch):
    module, fake_db = service
    calls = []

    def flaky_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if json["key_alias"].endswith("-frontier") and len(calls) <= 2:
            raise RuntimeError("LiteLLM down for Key 2")
        resp = MagicMock()
        resp.json.return_value = {
            "key": "sk-key2-plain" if json["key_alias"].endswith("-frontier") else "sk-key1-plain"
        }
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "post", flaky_post)

    # No raise: Key 1 is live, so the caller gets the true per-key state
    # plus a partial_failure marker instead of a 502-shaped exception.
    meta = module.provision_team_gateway_key("team-1")
    assert meta["keys"][0]["status"] == "active"
    assert meta["keys"][1]["status"] == "missing"
    assert meta["partial_failure"]["key"] == "key2"
    assert meta["partial_failure"]["label"] == "Key 2"
    assert "LiteLLM down for Key 2" in meta["partial_failure"]["error"]

    # Key 1 survived; Key 2 is recorded missing, not lost with the doc.
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert doc["status"] == "active"
    assert _fernet_for_test().decrypt(doc["key_ciphertext"].encode()).decode() == "sk-key1-plain"
    assert doc["key2_status"] == "missing"
    assert doc["key2_alias"] == "fall26-team-1-frontier"

    # Retry mints only Key 2.
    before = len(calls)
    meta = module.provision_team_gateway_key("team-1")
    assert len(calls) == before + 1
    assert calls[-1]["key_alias"] == "fall26-team-1-frontier"
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert doc["key2_status"] == "active"
    assert _fernet_for_test().decrypt(doc["key2_ciphertext"].encode()).decode() == "sk-key2-plain"
    assert meta["keys"][0]["status"] == "active"
    assert meta["keys"][1]["status"] == "active"
    assert "partial_failure" not in meta


def test_provision_mints_only_missing_key2(service, monkeypatch):
    module, fake_db = service
    _seed_key1_only(module, fake_db)
    fake_post = _alias_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.provision_team_gateway_key("team-1")

    assert len(fake_post.calls) == 1
    assert fake_post.calls[0]["key_alias"] == "fall26-team-1-frontier"
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert _fernet_for_test().decrypt(doc["key_ciphertext"].encode()).decode() == "sk-key1-plain"
    assert doc["key2_status"] == "active"
    assert meta["keys"][0]["status"] == "active"
    assert meta["keys"][1]["status"] == "active"


def test_provision_mints_only_missing_key1(service, monkeypatch):
    module, fake_db = service
    _seed_key2_only(module, fake_db)
    fake_post = _alias_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.provision_team_gateway_key("team-1")

    assert len(fake_post.calls) == 1
    assert fake_post.calls[0]["key_alias"] == "fall26-team-1"
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert doc["status"] == "active"
    assert doc["key2_status"] == "active"
    assert meta["keys"][0]["status"] == "active"


def test_provision_requires_master_key(service, monkeypatch):
    module, _ = service
    monkeypatch.delenv("LITELLM_MASTER_KEY")
    with pytest.raises(RuntimeError, match="LITELLM_MASTER_KEY"):
        module.provision_team_gateway_key("team-1")


def test_provision_requires_encryption_key(service, monkeypatch):
    module, _ = service
    monkeypatch.delenv("GATEWAY_KEY_ENCRYPTION_KEY")
    with pytest.raises(RuntimeError, match="GATEWAY_KEY_ENCRYPTION_KEY"):
        module.provision_team_gateway_key("team-1")


# ---------------------------------------------------------------------------
# rotate_team_gateway_key
# ---------------------------------------------------------------------------

def test_rotate_deletes_then_regenerates_same_alias(service, monkeypatch):
    module, fake_db = service
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        resp = MagicMock()
        if url.endswith("/key/delete"):
            assert json == {"keys": ["sk-team-plain-1"]}
            resp.json.return_value = {}
        else:
            assert url.endswith("/key/generate")
            assert json["key_alias"] == "fall26-team-1"
            resp.json.return_value = {"key": "sk-team-plain-2"}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "post", fake_post)

    # Seed an active key the way provision would.
    seed = _ok_generate(key="sk-team-plain-1")
    monkeypatch.setattr(module.requests, "post", seed)
    module.provision_team_gateway_key("team-1")
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.rotate_team_gateway_key("team-1", "key1")

    assert set(meta) == {"team_id", "keys"}
    assert meta["keys"][0]["key_alias"] == "fall26-team-1"
    assert meta["keys"][0]["status"] == "active"
    assert "key" not in meta["keys"][0]  # metadata only, no plaintext
    assert "spend" not in meta["keys"][0]
    assert calls[0][0].endswith("/key/delete")
    assert calls[1][0].endswith("/key/generate")
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    decrypted = Fernet(os.environ["GATEWAY_KEY_ENCRYPTION_KEY"]).decrypt(
        doc["key_ciphertext"].encode()
    ).decode()
    assert decrypted == "sk-team-plain-2"
    # Rotating Key 1 leaves Key 2 untouched.
    assert Fernet(os.environ["GATEWAY_KEY_ENCRYPTION_KEY"]).decrypt(
        doc["key2_ciphertext"].encode()
    ).decode() == "sk-team-plain-1"
    assert [k["label"] for k in meta["keys"]] == ["Key 1", "Key 2"]
    assert meta["keys"][1]["status"] == "active"


def test_rotate_key2_deletes_then_regenerates_frontier_alias_and_leaves_key1(service, monkeypatch):
    module, fake_db = service
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        resp = MagicMock()
        if url.endswith("/key/delete"):
            assert json == {"keys": ["sk-key2-plain"]}
            resp.json.return_value = {}
        else:
            assert url.endswith("/key/generate")
            assert json["key_alias"] == "fall26-team-1-frontier"
            assert json["models"] == ["muse-wonder", "kimi-k3"]
            assert json["max_budget"] == 20.0
            assert json["metadata"]["key_label"] == "key2"
            resp.json.return_value = {"key": "sk-key2-new"}
        resp.raise_for_status.return_value = None
        return resp

    seed = _alias_generate()
    monkeypatch.setattr(module.requests, "post", seed)
    module.provision_team_gateway_key("team-1")
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.rotate_team_gateway_key("team-1", key="key2")

    assert meta["keys"][1]["key_alias"] == "fall26-team-1-frontier"
    assert meta["keys"][1]["status"] == "active"
    assert calls[0][0].endswith("/key/delete")
    assert calls[1][0].endswith("/key/generate")
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    fernet = _fernet_for_test()
    assert fernet.decrypt(doc["key2_ciphertext"].encode()).decode() == "sk-key2-new"
    assert doc["key2_rotated_at"]
    # Rotating Key 2 leaves Key 1 untouched.
    assert fernet.decrypt(doc["key_ciphertext"].encode()).decode() == "sk-key1-plain"


def test_rotate_key2_refuses_without_active_key2(service, monkeypatch):
    module, fake_db = service
    _seed_key1_only(module, fake_db)
    with pytest.raises(RuntimeError, match="No active gateway key"):
        module.rotate_team_gateway_key("team-1", key="key2")


def test_rotate_rejects_unknown_key_name(service):
    module, _ = service
    with pytest.raises(ValueError, match="Unknown gateway key"):
        module.rotate_team_gateway_key("team-1", key="key3")


def test_rotate_refuses_without_active_key(service):
    module, _ = service
    with pytest.raises(RuntimeError, match="No active gateway key"):
        module.rotate_team_gateway_key("team-1", "key1")


# ---------------------------------------------------------------------------
# get_team_gateway_key
# ---------------------------------------------------------------------------

def _seed_active_key(module, monkeypatch, team_id="team-1", key="sk-team-plain-1"):
    fake_post = _ok_generate(key=key)
    monkeypatch.setattr(module.requests, "post", fake_post)
    module.provision_team_gateway_key(team_id)


def _team_exists(monkeypatch, module, fake_db, team_id="team-1", exists=True):
    fake_db.collections.setdefault("teams", {})
    if exists:
        fake_db.collections["teams"][team_id] = {"name": "Team 1"}


def test_get_403_for_non_member(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: False)

    payload, status = module.get_team_gateway_key("someone-else", "team-1")

    assert status == 403
    assert payload["error"] == "not_team_member"
    assert "key" not in payload


def test_get_404_for_unknown_team(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db, exists=False)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "nope")

    assert status == 404
    assert payload["error"] == "team_not_found"


def test_get_404_when_not_provisioned(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 404
    assert payload["error"] == "key_not_provisioned"
    assert payload["retryable"] is True


def test_get_returns_plaintext_and_spend_for_member(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    _seed_active_key(module, monkeypatch)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)

    def fake_info(url, params=None, headers=None, timeout=None):
        assert url.endswith("/key/info")
        assert params and params["key"].startswith("sk-")
        resp = MagicMock()
        resp.json.return_value = {"info": {"spend": 3.5}}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "get", fake_info)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert set(payload) == {"team_id", "keys"}
    assert payload["team_id"] == "team-1"
    assert [k["label"] for k in payload["keys"]] == ["Key 1", "Key 2"]
    assert payload["keys"][0]["key"] == "sk-team-plain-1"
    assert payload["keys"][0]["spend"] == 3.5
    assert payload["keys"][0]["status"] == "active"
    assert payload["keys"][1]["key"] == "sk-team-plain-1"
    assert payload["keys"][1]["spend"] == 3.5
    assert payload["keys"][1]["key_alias"] == "fall26-team-1-frontier"
    assert payload["keys"][1]["max_budget"] == 20.0


def test_get_returns_200_with_key2_missing_when_only_key1_exists(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    _seed_key1_only(module, fake_db)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)

    def fake_info(url, params=None, headers=None, timeout=None):
        assert url.endswith("/key/info")
        assert params and params["key"].startswith("sk-")
        resp = MagicMock()
        resp.json.return_value = {"info": {"spend": 1.0}}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "get", fake_info)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert set(payload) == {"team_id", "keys"}
    assert payload["keys"][0]["key"] == "sk-key1-plain"
    assert payload["keys"][0]["spend"] == 1.0
    key2 = payload["keys"][1]
    assert key2["label"] == "Key 2"
    assert key2["status"] == "missing"
    assert key2["key"] is None
    assert key2["spend"] is None
    assert key2["key_alias"] == "fall26-team-1-frontier"
    assert key2["models"] == ["muse-wonder", "kimi-k3"]
    assert key2["max_budget"] == 20.0


def test_get_decrypt_failure_on_one_key_does_not_500(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    seed = _alias_generate()
    monkeypatch.setattr(module.requests, "post", seed)
    module.provision_team_gateway_key("team-1")
    # Corrupt Key 2's ciphertext; Key 1 must still come back.
    fake_db.collections["team_gateway_keys"]["team-1"]["key2_ciphertext"] = "not-a-fernet-token"
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)

    def fake_info(url, params=None, headers=None, timeout=None):
        assert url.endswith("/key/info")
        assert params and params["key"].startswith("sk-")
        resp = MagicMock()
        resp.json.return_value = {"info": {"spend": 2.0}}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "get", fake_info)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert payload["keys"][0]["key"] == "sk-key1-plain"
    assert payload["keys"][0]["status"] == "active"
    assert payload["keys"][1]["status"] == "error"
    assert payload["keys"][1]["key"] is None
    assert payload["keys"][1]["spend"] is None


def test_spend_lookup_uses_get_with_query_param(service, monkeypatch):
    """LiteLLM's /key/info is GET-only; a POST answers 405 and every
    team's spend came back null (Nov 2026)."""
    module, _ = service
    calls = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["url"], calls["params"] = url, params
        resp = MagicMock()
        resp.json.return_value = {"key": "hashed", "info": {"spend": 4.25}}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "get", fake_get)
    monkeypatch.setattr(
        module.requests, "post",
        MagicMock(side_effect=AssertionError("/key/info must not be POSTed")),
    )

    assert module._key_spend_best_effort("sk-abc", "team-1") == 4.25
    assert calls["url"].endswith("/key/info")
    assert calls["params"] == {"key": "sk-abc"}


def test_get_spend_none_when_info_fails(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    _seed_active_key(module, monkeypatch)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)
    monkeypatch.setattr(
        module.requests, "get",
        MagicMock(side_effect=RuntimeError("LiteLLM down")),
    )

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert payload["keys"][0]["key"] == "sk-team-plain-1"
    assert payload["keys"][0]["spend"] is None
    assert payload["keys"][1]["key"] == "sk-team-plain-1"
    assert payload["keys"][1]["spend"] is None


def test_get_allows_admin_non_member(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    _seed_active_key(module, monkeypatch)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: False)
    monkeypatch.setattr(
        module.requests, "post",
        MagicMock(side_effect=RuntimeError("LiteLLM down")),
    )

    payload, status = module.get_team_gateway_key(
        "admin-propel-uuid", "team-1", is_admin=True
    )

    assert status == 200
    assert payload["keys"][0]["key"] == "sk-team-plain-1"
    assert payload["keys"][1]["key"] == "sk-team-plain-1"


# ---------------------------------------------------------------------------
# View-layer wiring (propelauth stub)
# ---------------------------------------------------------------------------

def _passthrough_decorator_factory(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@pytest.fixture
def views(monkeypatch):
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough_decorator_factory,
        require_user=_passthrough_decorator_factory(),
        optional_user=_passthrough_decorator_factory(),
    )
    stub.auth_user = LocalProxy(lambda: g.propelauth_current_user)
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    module = importlib.import_module(VIEWS_MODULE)
    yield module
    sys.modules.pop(VIEWS_MODULE, None)


@pytest.fixture
def app(views):
    flask_app = Flask(__name__)
    flask_app.register_blueprint(views.bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


HEADERS = {"Authorization": "Bearer test"}


def test_get_gateway_key_route_403s_non_member(client, monkeypatch):
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    monkeypatch.setattr(
        "api.teams.teams_views.get_team_gateway_key",
        lambda propel, team_id, is_admin=False: ({"error": "not_team_member"}, 403),
    )

    res = client.get("/api/team/team-1/gateway-key", headers=HEADERS)

    assert res.status_code == 403
    assert res.get_json()["error"] == "not_team_member"


def test_get_gateway_key_route_serves_member(client, monkeypatch):
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    monkeypatch.setattr(
        "api.teams.teams_views.get_team_gateway_key",
        lambda propel, team_id, is_admin=False: (
            {
                "team_id": team_id,
                "keys": [
                    {"label": "Key 1", "key": "sk-team-plain-1", "key_alias": "fall26-team-1"},
                    {"label": "Key 2", "key": "sk-team-plain-2", "key_alias": "fall26-team-1-frontier"},
                ],
            },
            200,
        ),
    )

    res = client.get("/api/team/team-1/gateway-key", headers=HEADERS)

    assert res.status_code == 200
    assert res.get_json()["keys"][0]["key"] == "sk-team-plain-1"
    assert res.get_json()["keys"][1]["key"] == "sk-team-plain-2"


def test_rotate_gateway_key_route_passes_key_name_and_400s_without_it(client, monkeypatch):
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    seen = {}

    def fake_rotate(team_id, key):
        seen["team_id"] = team_id
        seen["key"] = key
        return {"team_id": team_id, "keys": [{"label": "Key 1"}, {"label": "Key 2"}]}

    monkeypatch.setattr("api.teams.teams_views.rotate_team_gateway_key", fake_rotate)

    res = client.post(
        "/api/team/team-1/gateway-key/rotate",
        headers=HEADERS, json={"key": "key2"},
    )
    assert res.status_code == 200
    assert seen == {"team_id": "team-1", "key": "key2"}

    res = client.post(
        "/api/team/team-1/gateway-key/rotate",
        headers=HEADERS, json={"key": "key1"},
    )
    assert res.status_code == 200
    assert seen["key"] == "key1"

    # No body: the key name is required now, there is no default.
    res = client.post("/api/team/team-1/gateway-key/rotate", headers=HEADERS)
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid_key"


def test_rotate_gateway_key_route_400s_invalid_key(client, monkeypatch):
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    res = client.post(
        "/api/team/team-1/gateway-key/rotate",
        headers=HEADERS, json={"key": "key9"},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid_key"


def test_retry_gateway_key_route_provisions(client, monkeypatch):
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    seen = {}

    def fake_provision(team_id):
        seen["team_id"] = team_id
        return {"keys": [{"label": "Key 1"}, {"label": "Key 2"}]}

    monkeypatch.setattr("api.teams.teams_views.provision_team_gateway_key", fake_provision)

    res = client.post("/api/team/team-1/gateway-key/retry", headers=HEADERS)

    assert res.status_code == 200
    assert seen == {"team_id": "team-1"}
    assert len(res.get_json()["keys"]) == 2


def test_retry_gateway_key_route_returns_200_on_partial_failure(client, monkeypatch):
    # Key 1 minted, Key 2 failed: the admin UI must learn the true state, so
    # the route answers 200 with per-key statuses + partial_failure, not 502.
    monkeypatch.setattr(
        "services.hackathon_planning_service.is_admin", lambda user: False
    )
    monkeypatch.setattr(
        "api.teams.teams_views.provision_team_gateway_key",
        lambda team_id: {
            "team_id": team_id,
            "keys": [
                {"label": "Key 1", "status": "active"},
                {"label": "Key 2", "status": "missing"},
            ],
            "partial_failure": {"key": "key2", "label": "Key 2", "error": "LiteLLM down"},
        },
    )

    res = client.post("/api/team/team-1/gateway-key/retry", headers=HEADERS)

    assert res.status_code == 200
    body = res.get_json()
    assert body["keys"][1]["status"] == "missing"
    assert body["partial_failure"]["key"] == "key2"


def test_gateway_key_routes_registered(client):
    # The volunteer.admin decorator is stubbed as passthrough in tests; this
    # asserts the routes exist and are wired to the blueprint.
    assert "/api/team/<teamid>/gateway-key/rotate" in [
        str(r) for r in client.application.url_map.iter_rules()
    ]
    assert "/api/team/<teamid>/gateway-key/retry" in [
        str(r) for r in client.application.url_map.iter_rules()
    ]


# ---------------------------------------------------------------------------
# list_gateway_key_statuses (admin Teams table) — metadata only, never plaintext
# ---------------------------------------------------------------------------

def test_list_statuses_reports_active_pending_and_missing(service, monkeypatch):
    module, fake_db = service
    _seed_active_key(module, monkeypatch, team_id="team-active", key="sk-secret-1")
    fake_db.collections.setdefault("team_gateway_keys", {})["team-pending"] = {
        "team_id": "team-pending", "key_alias": "fall26-team-pending",
        "status": "pending", "created_at": "2026-10-07T00:00:00+00:00",
    }
    # An "active" doc with no ciphertext is unusable → reported as missing.
    fake_db.collections["team_gateway_keys"]["team-broken"] = {
        "team_id": "team-broken", "status": "active", "key_alias": "fall26-team-broken",
    }

    out = module.list_gateway_key_statuses(
        ["team-active", "team-pending", "team-broken", "team-unknown", "team-active", ""]
    )

    active = out["team-active"]["keys"]
    assert [k["label"] for k in active] == ["Key 1", "Key 2"]
    assert active[0]["status"] == "active"
    assert active[0]["key_alias"] == "fall26-team-active"
    assert active[0]["max_budget"] == module.GATEWAY_MAX_BUDGET
    assert active[0]["provisioned_at"]
    assert active[1]["status"] == "active"
    assert active[1]["key_alias"] == "fall26-team-active-frontier"
    assert active[1]["max_budget"] == 20.0
    pending = out["team-pending"]["keys"]
    assert pending[0]["status"] == "pending"
    assert pending[1]["status"] == "missing"
    broken = out["team-broken"]["keys"]
    assert broken[0]["status"] == "missing"
    assert broken[1]["status"] == "missing"
    unknown = out["team-unknown"]["keys"]
    assert unknown[0] == {
        "label": "Key 1",
        "key_alias": "fall26-team-unknown",
        "models": ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"],
        "max_budget": 15.0,
        "expires": "2026-11-16T01:00:00Z",
        "status": "missing",
        "provisioned_at": None,
        "rotated_at": None,
    }
    assert unknown[1] == {
        "label": "Key 2",
        "key_alias": "fall26-team-unknown-frontier",
        "models": ["muse-wonder", "kimi-k3"],
        "max_budget": 20.0,
        "expires": "2026-11-16T01:00:00Z",
        "status": "missing",
        "provisioned_at": None,
        "rotated_at": None,
    }
    assert "" not in out
    # Nothing secret leaves this function, and no plaintext field exists.
    dumped = json.dumps(out)
    assert "sk-secret-1" not in dumped
    assert "key_ciphertext" not in dumped
    assert all("key" not in entry for entry in out["team-active"]["keys"])
    assert all("spend" not in entry for entry in out["team-active"]["keys"])


def test_list_statuses_caps_batch(service, monkeypatch):
    module, _ = service
    ids = [f"t{i}" for i in range(module.MAX_STATUS_BATCH + 50)]
    out = module.list_gateway_key_statuses(ids)
    assert len(out) == module.MAX_STATUS_BATCH


def test_admin_gateway_keys_route_returns_statuses(client, monkeypatch):
    seen = {}

    def fake_list(team_ids):
        seen["ids"] = team_ids
        return {
            t: {"keys": [{"label": "Key 1", "status": "active" if t == "a" else "missing"}]}
            for t in team_ids
        }

    monkeypatch.setattr("api.teams.teams_views.list_gateway_key_statuses", fake_list)

    res = client.get("/api/team/admin/gateway-keys?team_ids=a,%20b,,c", headers=HEADERS)

    assert res.status_code == 200
    assert seen["ids"] == ["a", "b", "c"]
    assert res.get_json()["keys"]["a"]["keys"][0]["status"] == "active"


def test_admin_gateway_keys_route_is_not_shadowed_by_admin_teamid(client, monkeypatch):
    # /admin/<teamid> exists; the static /admin/gateway-keys must win.
    called = {"detail": False}
    monkeypatch.setattr(
        "api.teams.teams_views.list_gateway_key_statuses",
        lambda ids: {i: {"keys": [{"label": "Key 1", "status": "missing"}]} for i in ids},
    )
    import services.teams_service as ts
    monkeypatch.setattr(ts, "get_team_admin", lambda tid: called.__setitem__("detail", True) or None)

    res = client.get("/api/team/admin/gateway-keys?team_ids=x", headers=HEADERS)

    assert res.status_code == 200
    assert "keys" in res.get_json()
    assert called["detail"] is False


def test_admin_gateway_keys_route_400s_without_ids(client):
    res = client.get("/api/team/admin/gateway-keys", headers=HEADERS)
    assert res.status_code == 400
    assert res.get_json()["error"] == "team_ids_required"
