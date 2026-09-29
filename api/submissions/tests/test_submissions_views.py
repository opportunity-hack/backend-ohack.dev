"""
Route-level tests for the submissions blueprint. Copies the signature-check
fixture from api/volunteers/tests/test_volunteers_views.py: propelauth's
decorators don't inject the user into the view, so a `def view(user, ...)`
mismatch only surfaces at real Flask dispatch time, not at import time.
"""
import functools
import importlib
import inspect
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.submissions.submissions_views"
FAKE_USER = types.SimpleNamespace(user_id="hacker-propel-uuid", email="hacker@example.com")


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


def test_every_view_signature_matches_its_url_params(app):
    mismatches = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = inspect.unwrap(app.view_functions[rule.endpoint])
        declared = {
            name
            for name, p in inspect.signature(view).parameters.items()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }
        if declared != set(rule.arguments):
            mismatches.append((rule.rule, sorted(declared), sorted(rule.arguments)))
    assert mismatches == [], f"view params != URL params: {mismatches}"


def test_save_project_route_dispatches_with_token_identity(views, client, monkeypatch):
    service = MagicMock(return_value=({"success": True}, 200))
    monkeypatch.setattr(views, "save_project", service)
    monkeypatch.setattr(views, "is_admin", lambda user: False)

    res = client.post("/api/team/team-1/project", json={"project_tagline": "Hi"}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with(FAKE_USER.user_id, "team-1", {"project_tagline": "Hi"}, admin=False)


def test_submit_project_route_dispatches(views, client, monkeypatch):
    service = MagicMock(return_value=({"success": True, "status": "submitted"}, 200))
    monkeypatch.setattr(views, "submit_project", service)
    monkeypatch.setattr(views, "is_admin", lambda user: False)

    res = client.post("/api/team/team-1/project/submit", headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with(FAKE_USER.user_id, "team-1", admin=False)


def test_mentor_availability_route_dispatches(views, client, monkeypatch):
    service = MagicMock(return_value=({"success": True}, 200))
    monkeypatch.setattr(views, "set_mentor_help_wanted", service)
    monkeypatch.setattr(views, "is_admin", lambda user: False)

    res = client.post("/api/team/team-1/mentor-availability", json={"open": False}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with(FAKE_USER.user_id, "team-1", False, admin=False)


def test_submissions_window_route_is_public_and_dispatches(views, client, monkeypatch):
    service = MagicMock(return_value=({"state": "open"}, 200))
    monkeypatch.setattr(views, "get_submission_window_for_event", service)

    res = client.get("/api/hackathons/event-1/submissions/window")

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with("event-1")


def test_admin_flag_passed_through_when_org_permission_present(views, client, monkeypatch):
    service = MagicMock(return_value=({"success": True}, 200))
    monkeypatch.setattr(views, "save_project", service)
    monkeypatch.setattr(views, "is_admin", lambda user: True)

    res = client.post("/api/team/team-1/project", json={}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with(FAKE_USER.user_id, "team-1", {}, admin=True)


def test_remind_route_403s_without_admin_or_api_key(views, client, monkeypatch):
    monkeypatch.setattr(views, "is_admin", lambda user: False)
    monkeypatch.setenv("BACKEND_CRON_TOKEN", "secret-token")

    res = client.post("/api/hackathons/event-1/deadlines/remind", json={"hours_before": 24})

    assert res.status_code == 403


def test_remind_route_allows_admin(views, client, monkeypatch):
    monkeypatch.setattr(views, "is_admin", lambda user: True)
    service = MagicMock(return_value=({"success": True}, 200))
    monkeypatch.setattr(views, "send_deadline_reminders", service)

    res = client.post("/api/hackathons/event-1/deadlines/remind", json={"hours_before": 24}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with("event-1", "submission", 24, only_if_due=False, force=False, actor=FAKE_USER.user_id)


def test_remind_route_allows_api_key_without_login(views, client, monkeypatch):
    monkeypatch.setattr(views, "is_admin", lambda user: False)
    monkeypatch.setenv("BACKEND_CRON_TOKEN", "secret-token")
    service = MagicMock(return_value=({"success": True}, 200))
    monkeypatch.setattr(views, "send_deadline_reminders", service)

    res = client.post(
        "/api/hackathons/event-1/deadlines/remind",
        json={"hours_before": 6, "only_if_due": True},
        headers={"X-Api-Key": "secret-token"},
    )

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with("event-1", "submission", 6, only_if_due=True, force=False, actor="cron")


def test_remind_due_route_requires_api_key(views, client, monkeypatch):
    monkeypatch.setenv("BACKEND_CRON_TOKEN", "secret-token")
    res = client.post("/api/hackathons/deadlines/remind-due")
    assert res.status_code == 403


def test_remind_due_route_dispatches_with_valid_key(views, client, monkeypatch):
    monkeypatch.setenv("BACKEND_CRON_TOKEN", "secret-token")
    service = MagicMock(return_value=({"success": True, "results": []}, 200))
    monkeypatch.setattr(views, "send_due_reminders_for_current_events", service)

    res = client.post("/api/hackathons/deadlines/remind-due", headers={"X-Api-Key": "secret-token"})

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with()
