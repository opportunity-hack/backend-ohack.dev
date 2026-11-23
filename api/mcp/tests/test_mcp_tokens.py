"""Personal access tokens for POST /api/mcp: the service (MockFirestore,
PropelAuth lookup patched) and the routes (common.auth stubbed)."""
import importlib
import os
import sys
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask
from mockfirestore import MockFirestore

from api.mcp import tokens_service as svc
from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.mcp.mcp_views"
ADMIN = {"propel_id": "fake-propel-uuid", "email": "fake@example.com", "name": "Admin"}
OTHER = {"propel_id": "p-2", "email": "other@example.com", "name": "Other"}
ORG = "org-1"
SESSION = {"Authorization": "Bearer session-jwt", "X-Org-Id": ORG}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _identity(permissions=("volunteer.admin",)):
    return {"email": "live@example.com", "name": "Live Name", "permissions": list(permissions)}


@pytest.fixture
def db():
    database = MockFirestore()
    svc.clear_identity_cache()
    with patch.object(svc, "get_db", return_value=database):
        yield database
    svc.clear_identity_cache()


@pytest.fixture
def identity():
    """Patch the PropelAuth lookup; the test sets ``.return_value``."""
    with patch.object(svc, "_fetch_identity", return_value=_identity()) as fetch:
        yield fetch


def _mint(admin=ADMIN, **body):
    body.setdefault("name", "laptop")
    payload, status = svc.create_token(admin, ORG, body)
    assert status == 201, payload
    return payload["token"], payload["data"]


def _stored(db):
    return [doc.to_dict() for doc in db.collection(svc.COLLECTION).stream()]


# --- service ----------------------------------------------------------------


def test_create_returns_the_raw_token_once_and_stores_only_its_hash(db):
    raw, public = _mint()
    assert raw.startswith(svc.TOKEN_PREFIX) and len(raw) > 40
    assert public["status"] == "active" and public["name"] == "laptop"
    assert "token" not in public and "user_id" not in public
    assert raw not in repr(_stored(db))
    assert [doc.id for doc in db.collection(svc.COLLECTION).stream()] == [svc._hash(raw)]
    listed, status = svc.list_tokens(ADMIN)
    assert status == 200 and raw not in repr(listed)
    assert listed["data"][0]["token_hint"] == raw[: len(svc.TOKEN_PREFIX) + 4]


@pytest.mark.parametrize(
    "body, code",
    [
        ({}, "invalid_name"),
        ({"name": "   "}, "invalid_name"),
        ({"name": "x" * 61}, "invalid_name"),
        ({"name": "ok", "expiresInDays": 0}, "invalid_expiry"),
        ({"name": "ok", "expiresInDays": 91}, "invalid_expiry"),
        ({"name": "ok", "expiresInDays": True}, "invalid_expiry"),
        ({"name": "ok", "expiresInDays": "30"}, "invalid_expiry"),
    ],
)
def test_create_validation(db, body, code):
    payload, status = svc.create_token(ADMIN, ORG, body)
    assert status == 400 and payload["error"] == code
    assert _stored(db) == []


def test_create_needs_a_body_an_org_and_a_caller(db):
    assert svc.create_token(ADMIN, ORG, None)[1] == 400
    assert svc.create_token(ADMIN, "", {"name": "x"})[0]["error"] == "invalid_org"
    assert svc.create_token({}, ORG, {"name": "x"})[1] == 401


def test_active_token_cap_ignores_expired_and_other_people(db):
    for _ in range(svc.MAX_ACTIVE_PER_USER):
        _mint()
    payload, status = svc.create_token(ADMIN, ORG, {"name": "one more"})
    assert status == 409 and payload["error"] == "token_limit"
    _mint(admin=OTHER)
    with patch.object(svc, "_now", return_value=svc._now() + timedelta(days=91)):
        _mint()


def test_resolve_rejects_unknown_expired_and_non_token_values(db):
    raw, _ = _mint(expiresInDays=1)
    assert svc.resolve_token(raw)["user_id"] == ADMIN["propel_id"]
    assert svc.resolve_token(raw + "x") is None
    assert svc.resolve_token("eyJ.not.ours") is None
    assert svc.resolve_token(None) is None
    with patch.object(svc, "_now", return_value=svc._now() + timedelta(days=1, seconds=1)):
        assert svc.resolve_token(raw) is None


def test_last_used_is_stamped_at_most_once_per_interval(db):
    raw, _ = _mint()
    svc.resolve_token(raw)
    first = _stored(db)[0]["last_used_at"]
    assert first
    svc.resolve_token(raw)
    assert _stored(db)[0]["last_used_at"] == first
    later = svc._now() + timedelta(seconds=svc._LAST_USED_WRITE_SECONDS + 1)
    with patch.object(svc, "_now", return_value=later):
        svc.resolve_token(raw)
    assert _stored(db)[0]["last_used_at"] == later.isoformat()


def test_revoke_is_owner_only_and_immediate(db, identity):
    raw, public = _mint()
    assert svc.revoke_token(OTHER, public["id"])[1] == 404
    assert svc.user_for_token(raw) is not None
    assert svc.revoke_token(ADMIN, public["id"]) == ({"success": True}, 200)
    assert svc.user_for_token(raw) is None
    assert svc.revoke_token(ADMIN, public["id"])[1] == 404


def test_user_for_token_carries_live_identity_not_stored_values(db, identity):
    raw, public = _mint()
    identity.return_value = _identity(("checkin.admin",))
    user = svc.user_for_token(raw)
    assert user.user_id == ADMIN["propel_id"] and user.token_id == public["id"]
    assert user.email == "live@example.com" and user.name == "Live Name"
    info = user.org_id_to_org_member_info[ORG]
    assert info.user_has_permission("checkin.admin")
    assert not info.user_has_permission("volunteer.admin")
    identity.assert_called_once_with(ADMIN["propel_id"], ORG)


def test_user_for_token_refuses_a_missing_owner(db, identity):
    raw, _ = _mint()
    identity.return_value = None
    assert svc.user_for_token(raw) is None


def test_identity_is_cached_briefly_and_outages_are_never_cached(db, identity):
    raw, _ = _mint()
    identity.side_effect = svc.IdentityUnavailable()
    with pytest.raises(svc.IdentityUnavailable):
        svc.user_for_token(raw)
    identity.side_effect = None
    svc.user_for_token(raw)
    svc.user_for_token(raw)
    assert identity.call_count == 2


class _Meta(object):
    def __init__(self, **fields):
        self.email = "m@example.com"
        self.first_name = "Meta"
        self.last_name = "User"
        self.locked = False
        self.enabled = True
        self.org_id_to_org_info = {ORG: {"user_permissions": ["volunteer.admin"]}}
        self.__dict__.update(fields)


def _fetch_with(monkeypatch, meta=None, error=None):
    def fetch(user_id, include_orgs=False):
        assert include_orgs is True
        if error:
            raise error
        return meta

    stub = passthrough_auth_module()
    stub.auth.fetch_user_metadata_by_user_id = fetch
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    return svc._fetch_identity("u-1", ORG)


def test_fetch_identity_reads_permissions_for_the_token_org(monkeypatch):
    assert _fetch_with(monkeypatch, _Meta()) == {
        "email": "m@example.com",
        "name": "Meta User",
        "permissions": ["volunteer.admin"],
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"org_id_to_org_info": None},
        {"org_id_to_org_info": {"another-org": {"user_permissions": ["volunteer.admin"]}}},
        {"org_id_to_org_info": {ORG: {"user_role": "Admin"}}},
    ],
)
def test_fetch_identity_grants_nothing_outside_the_token_org(monkeypatch, fields):
    assert _fetch_with(monkeypatch, _Meta(**fields))["permissions"] == []


@pytest.mark.parametrize("fields", [{"locked": True}, {"enabled": False}])
def test_fetch_identity_refuses_locked_and_disabled_accounts(monkeypatch, fields):
    assert _fetch_with(monkeypatch, _Meta(**fields)) is None


def test_fetch_identity_missing_user_and_outage(monkeypatch):
    assert _fetch_with(monkeypatch, None) is None
    with pytest.raises(svc.IdentityUnavailable):
        _fetch_with(monkeypatch, error=RuntimeError("propelauth down"))


# --- routes -----------------------------------------------------------------


def _real_has_any_permission(user, permissions):
    for info in (getattr(user, "org_id_to_org_member_info", None) or {}).values():
        if any(info.user_has_permission(p) for p in permissions):
            return True
    # The stubbed session user has no org map: the stub gate already let it in.
    return not hasattr(user, "org_id_to_org_member_info")


@pytest.fixture
def client(monkeypatch, db, identity):
    """A Flask client over the MCP blueprint with an edge-like auth stub
    (no Authorization -> 401, no X-Org-Id -> 403) and real permission
    checks for token users."""
    stub = rejecting_auth_module()
    stub.user_has_any_permission = _real_has_any_permission
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client()
    sys.modules.pop(VIEWS, None)


def _bearer(raw):
    return {"Authorization": "Bearer " + raw}


def test_token_routes_reject_anonymous_and_orgless(client):
    for method, path in (
        ("get", "/api/mcp/tokens"),
        ("post", "/api/mcp/tokens"),
        ("delete", "/api/mcp/tokens/abc"),
    ):
        call = getattr(client, method)
        assert call(path).status_code == 401, (method, path)
        assert call(path, headers={"Authorization": "Bearer x"}).status_code == 403


def test_mint_then_call_mcp_with_the_token_and_no_org_header(client):
    created = client.post("/api/mcp/tokens", json={"name": "laptop"}, headers=SESSION)
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    raw = created.json["token"]

    listed = client.get("/api/mcp/tokens", headers=SESSION)
    assert [t["name"] for t in listed.json["data"]] == ["laptop"]
    assert raw not in listed.get_data(as_text=True)

    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(raw))
    assert r.status_code == 200 and "tools" in r.json["result"]

    token_id = created.json["data"]["id"]
    assert client.delete("/api/mcp/tokens/" + token_id, headers=SESSION).status_code == 200
    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(raw))
    assert r.status_code == 401 and r.json["error"] == "invalid_token"


def test_unknown_token_is_401(client):
    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(svc.TOKEN_PREFIX + "nope"))
    assert r.status_code == 401


def test_owner_without_an_admin_permission_is_403(client, identity):
    raw, _ = _mint()
    identity.return_value = _identity(("judge.admin",))
    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(raw))
    assert r.status_code == 403 and r.json["error"] == "forbidden"


def test_propelauth_outage_is_503_not_a_pass(client, identity):
    raw, _ = _mint()
    identity.side_effect = svc.IdentityUnavailable()
    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(raw))
    assert r.status_code == 503 and r.json["error"] == "identity_unavailable"


def test_tool_permissions_follow_the_owners_live_roles(client, identity):
    # checkin.admin passes the endpoint gate but holds no volunteer.admin
    # tool, so the meet tools must not be listed for that owner.
    raw, _ = _mint()
    identity.return_value = _identity(("checkin.admin",))
    r = client.post("/api/mcp", json=LIST_TOOLS, headers=_bearer(raw))
    assert r.status_code == 200
    assert not [t for t in r.json["result"]["tools"] if t["name"].startswith("meet_")]


def test_a_personal_token_cannot_manage_tokens(monkeypatch, db, identity):
    # The token routes sit behind the PropelAuth gate, which only accepts a
    # session JWT. Model that edge: a non-JWT bearer is a 401.
    stub = rejecting_auth_module()
    inner = stub.require_any_permission

    def jwt_only(perms, req_to_org_id=None):
        def decorator(fn):
            gated = inner(perms, req_to_org_id)(fn)

            def wrapper(*args, **kwargs):
                from flask import request

                if svc.TOKEN_PREFIX in (request.headers.get("Authorization") or ""):
                    return {"error": "unauthorized"}, 401
                return gated(*args, **kwargs)

            wrapper.__name__ = fn.__name__
            wrapper.__wrapped__ = fn
            return wrapper

        return decorator

    stub.require_any_permission = jwt_only
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    c = app.test_client()
    raw, public = _mint()
    headers = {"Authorization": "Bearer " + raw, "X-Org-Id": ORG}
    assert c.get("/api/mcp/tokens", headers=headers).status_code == 401
    assert c.post("/api/mcp/tokens", json={"name": "x"}, headers=headers).status_code == 401
    assert c.delete("/api/mcp/tokens/" + public["id"], headers=headers).status_code == 401
    assert len(_stored(db)) == 1
    sys.modules.pop(VIEWS, None)


def test_tool_call_audit_names_the_token(monkeypatch, db, identity):
    stub = rejecting_auth_module()
    stub.user_has_any_permission = _real_has_any_permission
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    audited = []
    monkeypatch.setattr(
        views, "_audit_tool_call", lambda name, caller, is_error: audited.append(caller)
    )
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    raw, public = _mint()
    app.test_client().post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        },
        headers=_bearer(raw),
    )
    assert audited[0]["token_id"] == public["id"]
    assert audited[0]["propel_id"] == ADMIN["propel_id"]
    sys.modules.pop(VIEWS, None)
