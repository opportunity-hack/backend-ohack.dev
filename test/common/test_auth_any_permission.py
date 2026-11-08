"""
common.auth.require_any_permission / user_has_any_permission — the any-of
gate used by /api/checkin. `propelauth_flask` is stubbed so the real module
can be imported without a PropelAuth round-trip.
"""
import importlib
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PROPEL_AUTH_URL", "https://auth.example")
os.environ.setdefault("PROPEL_AUTH_KEY", "k")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy


class _Org:
    def __init__(self, perms, broken=False):
        self.user_permissions = list(perms)
        self._broken = broken

    def user_has_permission(self, p):
        if self._broken:
            raise AttributeError("old SDK")
        return p in self.user_permissions


def _user(perms, broken=False, no_org=False):
    u = types.SimpleNamespace(user_id="u1", email="u@example.com")
    u.org_id_to_org_member_info = {} if no_org else {"org-1": _Org(perms, broken)}
    return u


@pytest.fixture
def auth(monkeypatch):
    fake = types.ModuleType("propelauth_flask")

    def require_user(fn):
        def wrapper(*a, **k):
            from flask import request
            if not request.headers.get("Authorization"):
                return {"error": "unauthorized"}, 401
            return fn(*a, **k)
        wrapper.__name__ = fn.__name__
        return wrapper

    fake.init_auth = lambda **_k: types.SimpleNamespace(require_user=require_user)
    fake.current_user = LocalProxy(lambda: getattr(g, "user", None))
    monkeypatch.setitem(sys.modules, "propelauth_flask", fake)
    sys.modules.pop("common.auth", None)
    mod = importlib.import_module("common.auth")
    yield mod
    sys.modules.pop("common.auth", None)


def test_user_has_any_permission(auth):
    assert auth.user_has_any_permission(_user(["checkin.admin"]), auth.CHECKIN_PERMISSIONS)
    assert auth.user_has_any_permission(_user(["volunteer.admin"]), auth.CHECKIN_PERMISSIONS)
    assert not auth.user_has_any_permission(_user(["heart.admin"]), auth.CHECKIN_PERMISSIONS)
    assert not auth.user_has_any_permission(_user([], no_org=True), auth.CHECKIN_PERMISSIONS)
    assert not auth.user_has_any_permission(None, auth.CHECKIN_PERMISSIONS)
    # fallback to the raw permission list when the method is missing
    assert auth.user_has_any_permission(_user(["checkin.admin"], broken=True), auth.CHECKIN_PERMISSIONS)


@pytest.fixture
def client(auth):
    app = Flask(__name__)

    @app.route("/x")
    @auth.require_any_permission(auth.CHECKIN_PERMISSIONS, req_to_org_id=auth.getOrgId)
    def x():
        return {"ok": True}

    @app.before_request
    def _set_user():
        from flask import request
        perms = request.headers.get("X-Test-Perms", "")
        g.user = _user([p for p in perms.split(",") if p])

    return app.test_client()


def test_decorator_gates(client):
    assert client.get("/x").status_code == 401
    assert client.get("/x", headers={"Authorization": "Bearer t"}).status_code == 403  # no org id
    base = {"Authorization": "Bearer t", "X-Org-Id": "org-1"}
    assert client.get("/x", headers=base).status_code == 403  # no permission
    assert client.get("/x", headers=dict(base, **{"X-Test-Perms": "heart.admin"})).status_code == 403
    assert client.get("/x", headers=dict(base, **{"X-Test-Perms": "checkin.admin"})).status_code == 200
    assert client.get("/x", headers=dict(base, **{"X-Test-Perms": "volunteer.admin"})).status_code == 200
