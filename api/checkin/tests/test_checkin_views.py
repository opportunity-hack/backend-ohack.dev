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


# ---------------------------------------------------------------------------
# POST /<event>/reset/<type> — volunteer.admin ONLY.

def _permission_auth_module(perms):
    """An auth stub that enforces permissions like PropelAuth would, for a
    user holding exactly `perms`."""
    import functools
    from flask import g, request
    from test.common.auth_stubs import passthrough_auth_module as _base, DEFAULT_FAKE_USER

    stub = _base()

    def _require(wanted):
        wanted = (wanted,) if isinstance(wanted, str) else tuple(wanted)

        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                if not request.headers.get("Authorization"):
                    return {"error": "unauthorized"}, 401
                if not request.headers.get("X-Org-Id") or not set(wanted) & set(perms):
                    return {"error": "forbidden"}, 403
                g.propelauth_current_user = DEFAULT_FAKE_USER
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    stub.auth.require_org_member_with_permission = lambda perm, req_to_org_id=None: _require(perm)
    stub.require_any_permission = lambda perms_, req_to_org_id=None: _require(perms_)
    return stub


def _reset_stub(views, calls):
    def fake_reset(e, t, p, volunteer_ids=None):
        calls.append({"event": e, "type": t, "by": p, "ids": volunteer_ids})
        return {"reset": len(volunteer_ids or ["a", "b"]), "ids": volunteer_ids or ["a", "b"], "skipped": []}
    views.checkin_service.reset_checkins = fake_reset
    views.checkin_service.MAX_RESET_IDS = 500


def test_reset_is_full_admin_only(load):
    views, client = load(_permission_auth_module(("checkin.admin",)))
    _reset_stub(views, [])
    # A desk helper can still check people in...
    assert client.post("/api/checkin/fall-2026/hacker/v1", headers=HEADERS).status_code == 200
    # ...but can never reset.
    r = client.post("/api/checkin/fall-2026/reset/mentor", headers=HEADERS, json={"all": True})
    assert r.status_code == 403

    views, client = load(_permission_auth_module(("volunteer.admin",)))
    calls = []
    _reset_stub(views, calls)
    r = client.post("/api/checkin/fall-2026/reset/Mentor", headers=HEADERS, json={"all": True})
    assert r.status_code == 200 and r.json["reset"] == 2
    assert calls == [{"event": "fall-2026", "type": "mentor", "by": "fake-propel-uuid", "ids": None}]


def test_reset_rejects_anonymous_and_orgless(load):
    _, client = load(rejecting_auth_module())
    path = "/api/checkin/fall-2026/reset/mentor"
    # The stub's org-permission decorator answers 403 before any token check
    # (PropelAuth itself answers 401); either way, never 200.
    assert client.post(path, json={"all": True}).status_code in (401, 403)
    assert client.post(path, json={"all": True}, headers={"Authorization": "Bearer x"}).status_code == 403


@pytest.mark.parametrize("body", [None, {}, {"all": False}, {"all": "yes"}, {"volunteer_ids": []}])
def test_reset_without_scope_is_400(load, body):
    views, client = load(passthrough_auth_module())
    calls = []
    _reset_stub(views, calls)
    kwargs = {"headers": HEADERS}
    if body is not None:
        kwargs["json"] = body
    r = client.post("/api/checkin/fall-2026/reset/mentor", **kwargs)
    assert r.status_code == 400 and r.json["error"] == "missing_scope"
    assert calls == []


def test_reset_validates_ids(load):
    views, client = load(passthrough_auth_module())
    calls = []
    _reset_stub(views, calls)
    path = "/api/checkin/fall-2026/reset/mentor"
    assert client.post(path, headers=HEADERS, json={"volunteer_ids": "v1"}).json["error"] == "invalid_ids"
    assert client.post(path, headers=HEADERS, json={"volunteer_ids": [1]}).json["error"] == "invalid_ids"
    r = client.post(path, headers=HEADERS, json={"volunteer_ids": [f"v{i}" for i in range(501)]})
    assert r.status_code == 400 and r.json["error"] == "too_many_ids"
    r = client.post(path, headers=HEADERS, json={"volunteer_ids": ["v1", "v2"]})
    assert r.status_code == 200 and calls[-1]["ids"] == ["v1", "v2"]


def test_reset_unknown_type_404(load):
    views, client = load(passthrough_auth_module())
    _reset_stub(views, [])
    assert client.post("/api/checkin/fall-2026/reset/organizer", headers=HEADERS,
                       json={"all": True}).status_code == 404
