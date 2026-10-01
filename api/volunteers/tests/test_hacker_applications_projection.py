"""
GET /api/hacker/applications/<event_id> backs findteam.js matchmaking. It used
to strip only email/ageRange/shirtSize/dietaryRestrictions and was reachable
anonymously, so phone numbers, deposit bookkeeping, sent-email logs, etc. went
out to anyone. The service now projects to HACKER_DIRECTORY_FIELDS and the
route requires a logged-in user.
"""
import functools
import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

os.environ.setdefault("ENVIRONMENT", "test")

import flask
import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

import services.volunteers_service as vs

VIEWS_MODULE = "api.volunteers.volunteers_views"
FAKE_USER = types.SimpleNamespace(user_id="hacker-propel-uuid", email="hacker@example.com")

HACKER_DOC = {
    "user_id": "propel-1",
    "name": "Ada",
    "github": "ada",
    "teamStatus": "I'd like to be matched with a team",
    "teamCode": "ROCKET",
    "isSelected": True,
    "skills": "python",
    "email": "ada@example.com",
    "phone": "+1 555 0100",
    "additionalInfo": "private note",
    "deposit_amount_cents": 2500,
    "sent_emails": [{"subject": "hi"}],
    "ageRange": "18-24",
}


def _mock_db_returning(docs):
    snaps = []
    for d in docs:
        s = MagicMock()
        s.to_dict.return_value = dict(d)
        s.id = "vol-1"
        snaps.append(s)
    db = MagicMock()
    db.collection.return_value.where.return_value.where.return_value.stream.return_value = snaps
    return db


def test_hackers_are_projected_to_directory_allowlist():
    with patch.object(vs, "get_db", return_value=_mock_db_returning([HACKER_DOC])):
        [hacker] = vs.get_all_hackers_by_event_id("event-1")

    assert set(hacker) <= vs.HACKER_DIRECTORY_FIELDS
    for key in ("user_id", "teamStatus", "isSelected", "teamCode"):
        assert key in hacker
    for key in ("phone", "additionalInfo", "deposit_amount_cents", "sent_emails", "email", "ageRange"):
        assert key not in hacker


def _passthrough(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _rejecting_require_user(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not flask.request.headers.get("Authorization"):
            return {"error": "unauthorized"}, 401
        g.propelauth_current_user = FAKE_USER
        return fn(*args, **kwargs)

    return wrapper


@pytest.fixture
def client(monkeypatch):
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough,
        require_user=_rejecting_require_user,
        optional_user=_passthrough(),
    )
    stub.auth_user = LocalProxy(lambda: g.get("propelauth_current_user"))
    stub.getOrgId = lambda req: req.headers.get("X-Org-Id")
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    views = importlib.import_module(VIEWS_MODULE)
    monkeypatch.setattr(views, "get_all_hackers_by_event_id", lambda event_id: [{"name": "Ada"}])

    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client()
    sys.modules.pop(VIEWS_MODULE, None)


def test_anonymous_request_is_rejected(client):
    response = client.get("/api/hacker/applications/event-1")
    assert response.status_code == 401


def test_logged_in_request_gets_directory(client):
    response = client.get("/api/hacker/applications/event-1", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
