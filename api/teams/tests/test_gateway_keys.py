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


# ---------------------------------------------------------------------------
# provision_team_gateway_key
# ---------------------------------------------------------------------------

def test_provision_mints_key_with_expected_body(service, monkeypatch):
    module, fake_db = service
    fake_post = _ok_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    meta = module.provision_team_gateway_key("team-1")

    assert meta["key_alias"] == "fall26-team-1"
    assert meta["models"] == ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"]
    assert meta["max_budget"] == 15.0
    assert len(fake_post.calls) == 1
    body = fake_post.calls[0]
    assert body["key_alias"] == "fall26-team-1"
    assert body["max_budget"] == 15.0
    assert "budget_duration" not in body  # lifetime cap, never resets
    assert body["expires"] == "2026-11-16T07:00:00Z"

    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    assert doc["status"] == "active"
    assert "sk-team-plain-1" not in json.dumps(doc)  # plaintext never stored raw
    decrypted = Fernet(os.environ["GATEWAY_KEY_ENCRYPTION_KEY"]).decrypt(
        doc["key_ciphertext"].encode()
    ).decode()
    assert decrypted == "sk-team-plain-1"


def test_provision_is_idempotent(service, monkeypatch):
    module, fake_db = service
    fake_post = _ok_generate()
    monkeypatch.setattr(module.requests, "post", fake_post)

    first = module.provision_team_gateway_key("team-1")
    second = module.provision_team_gateway_key("team-1")

    assert len(fake_post.calls) == 1  # second call minted nothing
    assert first == second


def test_provision_cleans_pending_doc_on_failure(service, monkeypatch):
    module, fake_db = service

    def boom(url, json=None, headers=None, timeout=None):
        raise RuntimeError("LiteLLM down")

    monkeypatch.setattr(module.requests, "post", boom)

    with pytest.raises(RuntimeError):
        module.provision_team_gateway_key("team-1")

    assert "team-1" not in fake_db.collections["team_gateway_keys"]


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

    meta = module.rotate_team_gateway_key("team-1")

    assert meta["key_alias"] == "fall26-team-1"
    assert calls[0][0].endswith("/key/delete")
    assert calls[1][0].endswith("/key/generate")
    doc = fake_db.collections["team_gateway_keys"]["team-1"]
    decrypted = Fernet(os.environ["GATEWAY_KEY_ENCRYPTION_KEY"]).decrypt(
        doc["key_ciphertext"].encode()
    ).decode()
    assert decrypted == "sk-team-plain-2"


def test_rotate_refuses_without_active_key(service):
    module, _ = service
    with pytest.raises(RuntimeError, match="No active gateway key"):
        module.rotate_team_gateway_key("team-1")


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

    def fake_info(url, json=None, headers=None, timeout=None):
        assert url.endswith("/key/info")
        resp = MagicMock()
        resp.json.return_value = {"info": {"spend": 3.5}}
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(module.requests, "post", fake_info)

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert payload["key"] == "sk-team-plain-1"
    assert payload["spend"] == 3.5
    assert payload["key_alias"] == "fall26-team-1"


def test_get_spend_none_when_info_fails(service, monkeypatch):
    module, fake_db = service
    _team_exists(monkeypatch, module, fake_db)
    _seed_active_key(module, monkeypatch)
    monkeypatch.setattr(module, "user_is_on_team", lambda propel, team: True)
    monkeypatch.setattr(
        module.requests, "post",
        MagicMock(side_effect=RuntimeError("LiteLLM down")),
    )

    payload, status = module.get_team_gateway_key("caller-propel-uuid", "team-1")

    assert status == 200
    assert payload["key"] == "sk-team-plain-1"
    assert payload["spend"] is None


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
    assert payload["key"] == "sk-team-plain-1"


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
            {"key": "sk-team-plain-1", "key_alias": "fall26-team-1"}, 200
        ),
    )

    res = client.get("/api/team/team-1/gateway-key", headers=HEADERS)

    assert res.status_code == 200
    assert res.get_json()["key"] == "sk-team-plain-1"


def test_gateway_key_routes_registered(client):
    # The volunteer.admin decorator is stubbed as passthrough in tests; this
    # asserts the routes exist and are wired to the blueprint.
    assert "/api/team/<teamid>/gateway-key/rotate" in [
        str(r) for r in client.application.url_map.iter_rules()
    ]
    assert "/api/team/<teamid>/gateway-key/retry" in [
        str(r) for r in client.application.url_map.iter_rules()
    ]
