"""Route-level tests: client-caused 400s must not page Sentry as server errors.

Sentry (Oct 2026 weekly report, backend-ohack-prod, 2 events):
  "Error in admin_get_resend_email_statuses: 400 Bad Request: The browser
   (or proxy) sent a request that this server could not understand."

Two bugs combined to produce it:

1. _process_request() called request.get_json() without silent=True, so a
   malformed JSON body raised Werkzeug BadRequest. The view's blanket
   `except Exception` then logged it with logger.error + logger.exception,
   which Sentry records as an error. A client sending bad JSON is not a
   server bug and must not page Sentry.

2. When the body was empty, _process_request() raised
   InvalidUsageError("Missing request body", status_code=400) -- but
   InvalidUsageError.__init__ does not accept status_code, so it raised
   TypeError instead and the client got a 500.

After the fix: malformed/empty bodies return a clean 400 via
InvalidUsageError, logged at warning level only.
"""
import functools
import importlib
import logging
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.volunteers.volunteers_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}
URL = "/api/admin/emails/resend-status"


def _stub_module(monkeypatch, name, path=None, **attrs):
    """Install a stub module for the duration of ONE test.

    Must go through monkeypatch: a bare ``sys.modules[name] = mod`` leaked the
    stubbed ``services`` / ``services.volunteers_service`` packages into every
    test file collected after this one (``test_resend_utils.py``,
    ``test_volunteers_service.py``), which then failed with
    ``module 'services' has no attribute 'volunteers_service'`` in CI.
    """
    mod = types.ModuleType(name)
    if path:
        mod.__path__ = path
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def views(monkeypatch):
    _stub_module(monkeypatch, "api", path=["api"])
    _stub_module(monkeypatch, "api.volunteers", path=["api/volunteers"])
    _stub_module(monkeypatch, "services", path=["services"])
    # The view imports many service functions; a MagicMock module supplies them.
    _stub_module(monkeypatch, "services.volunteers_service", __getattr__=MagicMock())
    _stub_module(monkeypatch, "common.utils.slack", send_slack_audit=MagicMock())

    stub_auth = types.ModuleType("common.auth")

    def _passthrough(*_args, **_kwargs):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                g.propelauth_current_user = types.SimpleNamespace(
                    user_id="admin-propel-uuid", email="admin@example.com"
                )
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    stub_auth.auth = types.SimpleNamespace(
        require_user=_passthrough(),
        require_org_member_with_permission=_passthrough,
        optional_user=_passthrough(),
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


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_malformed_json_returns_400_without_error_log(views, client, caplog):
    """Malformed JSON is a client mistake: 400, and nothing at ERROR level
    (ERROR logs are what Sentry turns into error events)."""
    with caplog.at_level(logging.DEBUG, logger=views.logger.name):
        res = client.post(
            URL, data="{not valid json", content_type="application/json", headers=HEADERS
        )

    assert res.status_code == 400, res.get_data(as_text=True)
    assert _error_records(caplog) == [], (
        "client-caused 400 was logged at ERROR level and would page Sentry: "
        f"{[r.getMessage() for r in _error_records(caplog)]}"
    )


def test_empty_body_returns_400_not_500(views, client, caplog):
    """Empty body must be a clean 400, not a TypeError -> 500."""
    with caplog.at_level(logging.DEBUG, logger=views.logger.name):
        res = client.post(URL, data="", content_type="application/json", headers=HEADERS)

    assert res.status_code == 400, res.get_data(as_text=True)
    assert _error_records(caplog) == [], (
        "empty body produced ERROR-level logs: "
        f"{[r.getMessage() for r in _error_records(caplog)]}"
    )


def test_invalid_usage_error_accepts_status_code():
    """InvalidUsageError("...", status_code=400) must not raise TypeError."""
    from common.exceptions import InvalidUsageError

    err = InvalidUsageError("Missing request body", status_code=400)
    assert err.status_code == 400
    assert "Missing request body" in str(err)
