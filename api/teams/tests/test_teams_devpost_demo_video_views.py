"""
Regression tests for Part 9 bug #1: POST /api/team/<id>/devpost and
POST /api/team/<id>/demo-video used to call edit_team directly with NO
membership check, so any logged-in user could overwrite any team's Devpost
link or demo video. They now route through
api.submissions.submissions_service.self_serve_team_edit, which enforces
team membership (or admin) and the submission deadline. These tests check
the view-layer wiring; self_serve_team_edit's own gating logic is covered in
api/submissions/tests/test_submissions_service.py.

Uses the same propelauth-stub pattern as
api/volunteers/tests/test_volunteers_views.py.
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

VIEWS_MODULE = "api.teams.teams_views"
FAKE_USER = types.SimpleNamespace(user_id="caller-propel-uuid", email="caller@example.com")


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


def test_devpost_route_403s_non_member(client, monkeypatch):
    """The actual regression: a non-member must be rejected, not silently
    allowed to overwrite the team's Devpost link."""
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    monkeypatch.setattr(
        "api.submissions.submissions_service._authorize_team_write",
        lambda propel, team_id, admin=False, enforce_deadline=True: (({"error": "not_team_member"}, 403), None, None, None),
    )

    res = client.post("/api/team/team-1/devpost", json={"devpost_link": "https://devpost.com/x"}, headers=HEADERS)

    assert res.status_code == 403
    assert res.get_json()["error"] == "not_team_member"


def test_devpost_route_allows_member(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    monkeypatch.setattr(
        "api.submissions.submissions_service.self_serve_team_edit",
        MagicMock(return_value={"success": True, "team": {"id": "team-1"}}),
    )

    res = client.post("/api/team/team-1/devpost", json={"devpost_link": "https://devpost.com/x"}, headers=HEADERS)

    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_devpost_route_requires_link(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    res = client.post("/api/team/team-1/devpost", json={}, headers=HEADERS)
    assert res.status_code == 400


def test_demo_video_route_403s_non_member(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    monkeypatch.setattr(
        "api.submissions.submissions_service.self_serve_team_edit",
        lambda propel, team_id, fields, admin=False: ({"error": "not_team_member"}, 403),
    )

    res = client.post("/api/team/team-1/demo-video", json={"demo_video_url": "https://youtu.be/x"}, headers=HEADERS)

    assert res.status_code == 403


def test_demo_video_route_allows_member_and_passes_admin_flag(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: True)
    service = MagicMock(return_value={"success": True, "team": {"id": "team-1"}})
    monkeypatch.setattr("api.submissions.submissions_service.self_serve_team_edit", service)

    res = client.post("/api/team/team-1/demo-video", json={"demo_video_url": ""}, headers=HEADERS)

    assert res.status_code == 200
    service.assert_called_once_with(FAKE_USER.user_id, "team-1", {"demo_video_url": ""}, admin=True)
