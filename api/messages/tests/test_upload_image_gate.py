"""
Route-level check that POST /api/messages/upload-image refuses to write into
another team's teams/<id>/ directory. Uses the same stub-common.auth trick as
api/submissions/tests/test_submissions_views.py so the real Flask dispatch
(and the view's own wiring of the gate) is exercised.
"""
import functools
import importlib
import io
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.messages.messages_views"
FAKE_USER = types.SimpleNamespace(user_id="hacker-propel-uuid", email="hacker@example.com")


def _passthrough(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@pytest.fixture
def client(monkeypatch):
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough,
        require_user=_passthrough(),
        optional_user=_passthrough(),
    )
    stub.auth_user = LocalProxy(lambda: g.propelauth_current_user)
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    views = importlib.import_module(VIEWS_MODULE)

    uploads = []
    monkeypatch.setattr(
        "api.messages.messages_service.upload_image_to_cdn",
        lambda request: uploads.append(request.form.get("directory")) or {"success": True, "url": "https://cdn.test/x.png"},
    )
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)

    app = Flask(__name__)
    app.register_blueprint(views.bp)
    test_client = app.test_client()
    test_client.uploads = uploads
    yield test_client
    sys.modules.pop(VIEWS_MODULE, None)


def _post(client, directory):
    return client.post(
        "/api/messages/upload-image",
        data={"file": (io.BytesIO(b"x"), "thumb.png"), "directory": directory},
        content_type="multipart/form-data",
    )


def test_non_member_cannot_upload_into_a_teams_directory(client, monkeypatch):
    monkeypatch.setattr("api.teams.teams_service.user_is_on_team", lambda propel, team_id: False)
    response = _post(client, "teams/someone-elses-team/project")
    assert response.status_code == 403
    assert response.get_json()["error"] == "not_team_member"
    assert client.uploads == []


def test_member_can_upload_into_their_teams_directory(client, monkeypatch):
    monkeypatch.setattr("api.teams.teams_service.user_is_on_team", lambda propel, team_id: team_id == "my-team")
    response = _post(client, "teams/my-team/project")
    assert response.status_code == 200
    assert client.uploads == ["teams/my-team/project"]


def test_other_directories_are_unaffected(client, monkeypatch):
    monkeypatch.setattr("api.teams.teams_service.user_is_on_team", lambda propel, team_id: False)
    response = _post(client, "nonprofits")
    assert response.status_code == 200
    assert client.uploads == ["nonprofits"]
