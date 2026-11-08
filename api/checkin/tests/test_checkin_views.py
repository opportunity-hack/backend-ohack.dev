"""
Route gates for /api/checkin (401 without a token, 403 without X-Org-Id, via
the REJECTING common.auth stub), the type validation, and the error mapping.
The service module is stubbed so the views import without Firestore.
"""
import importlib
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.checkin.checkin_views"
HEADERS = {"Authorization": "Bearer x", "X-Org-Id": "org-1"}


def _service_stub():
    svc = types.ModuleType("api.checkin.checkin_service")

    class CheckInError(Exception):
        def __init__(self, status, error, message):
            super().__init__(message)
            self.status, self.error, self.message = status, error, message

    svc.CheckInError = CheckInError
    svc.VOLUNTEER_TYPES = ("mentor", "judge", "volunteer", "hacker", "sponsor")
    svc.is_valid_type = lambda t: (t or "").lower() in svc.VOLUNTEER_TYPES
    svc.get_checkin_roster = lambda e, t: [{"id": "v1", "name": "Ada", "checkedIn": False}]
    svc.get_checked_in = lambda e, t: []
    svc.check_in_volunteer = lambda e, t, v, p: {"data": {"id": v, "checkedIn": True}, "already_checked_in": False, "by": p}
    return svc


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        stub = _service_stub()
        monkeypatch.setitem(sys.modules, "api.checkin.checkin_service", stub)
        # `from api.checkin import checkin_service` prefers the package attribute
        # (set when another test imported the real service in this process).
        import api.checkin as pkg
        monkeypatch.setattr(pkg, "checkin_service", stub, raising=False)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app.test_client()

    yield _load
    sys.modules.pop(VIEWS, None)


def test_every_view_signature_matches_its_url_params(load):
    views, _ = load(passthrough_auth_module())
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        fn = app.view_functions[rule.endpoint]
        import inspect
        params = set(inspect.signature(inspect.unwrap(fn)).parameters)
        assert params == set(rule.arguments), rule


@pytest.mark.parametrize("method,path", [
    ("get", "/api/checkin/fall-2026/roster/hacker"),
    ("get", "/api/checkin/fall-2026/checkins/hacker"),
    ("post", "/api/checkin/fall-2026/hacker/v1"),
])
def test_routes_reject_anonymous_and_orgless(load, method, path):
    _, client = load(rejecting_auth_module())
    call = getattr(client, method)
    assert call(path).status_code == 401
    assert call(path, headers={"Authorization": "Bearer x"}).status_code == 403
    assert call(path, headers=HEADERS).status_code == 200


def test_roster_and_checkins_shape(load):
    _, client = load(passthrough_auth_module())
    r = client.get("/api/checkin/fall-2026/roster/hacker", headers=HEADERS)
    assert r.status_code == 200 and r.json == {"data": [{"id": "v1", "name": "Ada", "checkedIn": False}], "count": 1}
    r = client.get("/api/checkin/fall-2026/checkins/Mentor", headers=HEADERS)
    assert r.status_code == 200 and r.json == {"data": [], "count": 0}


def test_unknown_type_404(load):
    _, client = load(passthrough_auth_module())
    assert client.get("/api/checkin/fall-2026/roster/organizer", headers=HEADERS).status_code == 404
    assert client.post("/api/checkin/fall-2026/organizer/v1", headers=HEADERS).status_code == 404


def test_check_in_passes_caller_and_maps_errors(load):
    views, client = load(passthrough_auth_module())
    r = client.post("/api/checkin/fall-2026/hacker/v1", headers=HEADERS)
    assert r.status_code == 200
    assert r.json["data"]["id"] == "v1" and r.json["by"] == "fake-propel-uuid"

    def boom(e, t, v, p):
        raise views.checkin_service.CheckInError(409, "wrong_event", "nope")

    views.checkin_service.check_in_volunteer = boom
    r = client.post("/api/checkin/fall-2026/hacker/v1", headers=HEADERS)
    assert r.status_code == 409 and r.json == {"error": "wrong_event", "message": "nope"}
