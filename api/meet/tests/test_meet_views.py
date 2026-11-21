"""Route tests for /api/meet (fixture copied from the reimbursements route
tests: common.auth and the service are stubbed, dispatch through Flask)."""
import importlib
import inspect
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.meet.meet_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}

ADMIN_ROUTES = [
    ("get", "/api/meet/admin/polls"),
    ("post", "/api/meet/admin/polls"),
    ("get", "/api/meet/admin/polls/abc"),
    ("patch", "/api/meet/admin/polls/abc"),
    ("delete", "/api/meet/admin/polls/abc"),
    ("delete", "/api/meet/admin/polls/abc/votes/v1"),
    ("post", "/api/meet/admin/polls/abc/finalize"),
]


def _service_stub():
    svc = types.ModuleType("api.meet.meet_service")
    svc.get_public_poll = lambda code: ({"poll": {"code": code}, "votes": []}, 200)
    svc.submit_vote = lambda code, data: ({"success": True, "voteId": "v1"}, 201)
    svc.admin_list_polls = lambda: ({"data": [], "calendar_configured": False}, 200)
    svc.admin_create_poll = lambda data, admin: ({"success": True, "admin": admin}, 201)
    svc.admin_get_poll = lambda code: ({"poll": {"code": code}, "votes": []}, 200)
    svc.admin_update_poll = lambda code, data: ({"success": True}, 200)
    svc.admin_delete_poll = lambda code: ({"success": True}, 200)
    svc.admin_delete_vote = lambda code, vote_id: ({"success": True, "id": vote_id}, 200)
    svc.admin_finalize_poll = lambda code, data, admin: ({"success": True}, 200)
    return svc


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        stub = _service_stub()
        monkeypatch.setitem(sys.modules, "api.meet.meet_service", stub)
        import api.meet as pkg
        monkeypatch.setattr(pkg, "meet_service", stub, raising=False)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app

    yield _load
    sys.modules.pop(VIEWS, None)


def test_every_view_signature_matches_its_url_params(load):
    _, app = load(passthrough_auth_module())
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        fn = app.view_functions[rule.endpoint]
        params = set(inspect.signature(inspect.unwrap(fn)).parameters)
        assert params == set(rule.arguments), rule


def test_public_routes_open_without_auth(load):
    client = load(rejecting_auth_module())[1].test_client()
    r = client.get("/api/meet/1234")
    assert r.status_code == 200 and r.json["poll"]["code"] == "1234"
    assert client.post("/api/meet/1234/votes", json={"name": "Ada"}).status_code == 201


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_admin_routes_reject_anonymous_and_orgless(load, method, path):
    client = load(rejecting_auth_module())[1].test_client()
    call = getattr(client, method)
    kwargs = {"json": {"title": "x"}} if method in ("post", "patch") else {}
    assert call(path, **kwargs).status_code == 401
    assert call(path, headers={"Authorization": "Bearer <redacted>"}, **kwargs).status_code == 403
    assert call(path, headers=HEADERS, **kwargs).status_code in (200, 201)


def test_admin_list_is_not_shadowed_by_the_code_route(load):
    client = load(passthrough_auth_module())[1].test_client()
    r = client.get("/api/meet/admin/polls", headers=HEADERS)
    assert r.status_code == 200 and r.json["data"] == []


def test_service_exception_is_a_json_500(load):
    views, app = load(passthrough_auth_module())

    def boom(code):
        raise RuntimeError("firestore down")

    views.meet_service.get_public_poll = boom
    r = app.test_client().get("/api/meet/1234")
    assert r.status_code == 500 and r.json["error"] == "internal_error"
