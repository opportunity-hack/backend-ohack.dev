"""Route-level test: GET /api/users/<id>/profile must 404 when the profile is missing.

Sentry (Oct 2026 weekly report, backend-ohack-prod, 170 events):
  TypeError: The view function for 'api-users.get_profile_by_db_id' did not
  return a valid response. The function either returned None or ended without
  a return statement.

Root cause: the view returned None when the service found no profile, and
Flask cannot turn None into a response, so every lookup of a missing profile
became a 500. The view must return a 404 JSON body instead.
"""
import functools
import importlib
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.users.users_views"


def _stub_module(monkeypatch, name, path=None, **attrs):
    # Through monkeypatch so the stubbed `api`/`services`/`model` packages are
    # restored after each test instead of leaking into later test files.
    mod = types.ModuleType(name)
    if path:
        mod.__path__ = path
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def views(monkeypatch):
    # Skip api/__init__.py (flask_cors, talisman, ...) and the heavy service
    # modules; the view under test only needs the service function mocked.
    _stub_module(monkeypatch, "api", path=["api"])
    _stub_module(monkeypatch, "api.users", path=["api/users"])
    _stub_module(monkeypatch, "services", path=["services"])
    _stub_module(monkeypatch, "services.users_service", get_profile_by_db_id=MagicMock())
    _stub_module(monkeypatch, "services.user_slug_service")
    _stub_module(monkeypatch, "services.problem_statements_service")
    _stub_module(monkeypatch, "model", path=["model"])
    _stub_module(monkeypatch, "model.user", User=object)

    stub_auth = types.ModuleType("common.auth")

    def _passthrough(*_args, **_kwargs):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                g.propelauth_current_user = types.SimpleNamespace(user_id="u1")
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    stub_auth.auth = types.SimpleNamespace(
        require_user=_passthrough(),
        require_org_member_with_permission=_passthrough,
    )
    stub_auth.auth_user = LocalProxy(lambda: g.get("propelauth_current_user"))
    stub_auth.getOrgId = lambda req: req.headers.get("X-Org-Id")
    monkeypatch.setitem(sys.modules, "common.auth", stub_auth)

    sys.modules.pop(VIEWS_MODULE, None)
    module = importlib.import_module(VIEWS_MODULE)
    yield module
    sys.modules.pop(VIEWS_MODULE, None)


@pytest.fixture
def client(views):
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    return app.test_client()


def test_missing_profile_returns_404_not_500(views, client, monkeypatch):
    monkeypatch.setattr(
        views.users_service, "get_profile_by_db_id", MagicMock(return_value=None)
    )

    res = client.get("/api/users/does-not-exist/profile")

    assert res.status_code == 404, (
        f"expected 404 for a missing profile, got {res.status_code}: "
        "the view must not return None"
    )
    assert res.get_json()["error"] == "Profile not found"


def test_existing_profile_still_returned(views, client, monkeypatch):
    profile = {"id": "abc123", "name": "Test User"}
    monkeypatch.setattr(
        views.users_service, "get_profile_by_db_id", MagicMock(return_value=profile)
    )

    res = client.get("/api/users/abc123/profile")

    assert res.status_code == 200
    assert res.get_json() == profile
