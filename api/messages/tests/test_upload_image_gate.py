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
    overwrite_flags = []

    def _fake_upload(request, allow_overwrite=True):
        uploads.append(request.form.get("directory"))
        overwrite_flags.append(allow_overwrite)
        return {"success": True, "url": "https://cdn.test/x.png"}

    monkeypatch.setattr("api.messages.messages_service.upload_image_to_cdn", _fake_upload)
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)

    app = Flask(__name__)
    app.register_blueprint(views.bp)
    test_client = app.test_client()
    test_client.uploads = uploads
    test_client.overwrite_flags = overwrite_flags
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


def test_non_admin_cannot_write_shared_site_directories(client, monkeypatch):
    """Was 200: any logged-in user could (over)write site assets like
    ohack.dev/logos or an event's photo gallery. (Replaces the old
    `test_other_directories_are_unaffected`, which asserted exactly that.)"""
    monkeypatch.setattr("api.teams.teams_service.user_is_on_team", lambda propel, team_id: False)
    for directory in ("ohack.dev/logos", "hackathons/x/photos", "nonprofits", "images/nested"):
        response = _post(client, directory)
        assert response.status_code == 403, directory
        assert response.get_json()["error"] == "directory_not_allowed"
    assert client.uploads == []


def test_non_admin_can_write_application_photo_directories(client):
    for directory in ("hackers", "mentors", "judges", "volunteers", "sponsors", "images", "uploads"):
        assert _post(client, directory).status_code == 200, directory
    assert client.overwrite_flags == [False] * 7


def test_missing_directory_defaults_to_images(client):
    response = client.post(
        "/api/messages/upload-image",
        data={"file": (io.BytesIO(b"x"), "thumb.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200


def test_admin_may_write_any_valid_directory_and_overwrite(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: True)
    assert _post(client, "ohack.dev/logos").status_code == 200
    assert client.overwrite_flags == [True]


def test_traversal_and_odd_characters_rejected(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: True)
    for directory in ("../x", "hackers/../teams/t", "a b", "x;rm"):
        response = _post(client, directory)
        assert response.status_code == 400, directory
        assert response.get_json()["error"] == "invalid_directory"
    assert client.uploads == []


def test_planning_editor_can_attach_files_to_their_events_cards(client, monkeypatch):
    """Non-admin planning editors upload card attachments under
    hackathons/<event>/planning/cards/<card>; the per-event editor check
    (services.hackathon_planning_service.can_write_plan_for_event) admits them.
    Other subtrees of the same event stay admin-only."""
    monkeypatch.setattr(
        "services.hackathon_planning_service.can_write_plan_for_event",
        lambda user, event_id: event_id == "2026_fall",
    )
    assert _post(client, "hackathons/2026_fall/planning/cards/abc").status_code == 200
    assert _post(client, "hackathons/other_event/planning/cards/abc").status_code == 403
    assert _post(client, "hackathons/2026_fall/photos").status_code == 403
    assert client.uploads == ["hackathons/2026_fall/planning/cards/abc"]
    assert client.overwrite_flags == [False]
