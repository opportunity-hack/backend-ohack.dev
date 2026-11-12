"""Route gates for /api/giveaways (401 anonymous, 403 org-less admin calls via
the REJECTING common.auth stub) and the GiveawayError -> JSON mapping. The
service is stubbed so the views import without Firestore."""
import importlib
import inspect
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.giveaways.giveaways_views"
HEADERS = {"Authorization": "Bearer x", "X-Org-Id": "org-1"}


def _service_stub():
    svc = types.ModuleType("api.giveaways.giveaways_service")

    class GiveawayError(Exception):
        def __init__(self, status, error, message):
            super().__init__(message)
            self.status, self.error, self.message = status, error, message

    def closed(*_a):
        raise GiveawayError(409, "giveaway_closed", "Entries for this giveaway are closed.")

    svc.GiveawayError = GiveawayError
    svc.get_open_giveaways = lambda pid: {"giveaways": [], "who": pid}
    svc.save_entry = closed
    svc.withdraw_entry = lambda pid, gid: {"withdrawn": True}
    svc.admin_list_campaigns = lambda: {"giveaways": []}
    svc.admin_create_campaign = lambda body, actor: {"id": body.get("id"), "by": actor}
    svc.admin_get_campaign = lambda gid: {"id": gid}
    svc.admin_update_campaign = lambda gid, body, actor: {"id": gid}
    svc.admin_list_entries = lambda gid: {"entries": [], "count": 0}
    svc.admin_start_draw = lambda gid, body, actor: {"id": gid}
    svc.admin_record_result = lambda gid, body, actor: {"result": None}
    svc.admin_undo_result = lambda gid, body, actor: {"id": gid}
    svc.admin_reset_draw = lambda gid, actor: {"id": gid}
    return svc


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        stub = _service_stub()
        monkeypatch.setitem(sys.modules, "api.giveaways.giveaways_service", stub)
        import api.giveaways as pkg
        monkeypatch.setattr(pkg, "giveaways_service", stub, raising=False)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app

    yield _load
    sys.modules.pop(VIEWS, None)


def test_view_signatures_match_url_params(load):
    _, app = load(passthrough_auth_module())
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        params = set(inspect.signature(inspect.unwrap(app.view_functions[rule.endpoint])).parameters)
        assert params == set(rule.arguments), rule


ROUTES = [
    ("get", "/api/giveaways/open"),
    ("post", "/api/giveaways/fall-2026/entry"),
    ("delete", "/api/giveaways/fall-2026/entry"),
    ("get", "/api/giveaways/admin"),
    ("post", "/api/giveaways/admin"),
    ("get", "/api/giveaways/admin/fall-2026"),
    ("patch", "/api/giveaways/admin/fall-2026"),
    ("get", "/api/giveaways/admin/fall-2026/entries"),
    ("post", "/api/giveaways/admin/fall-2026/draw/start"),
    ("post", "/api/giveaways/admin/fall-2026/draw/results"),
    ("post", "/api/giveaways/admin/fall-2026/draw/undo"),
    ("post", "/api/giveaways/admin/fall-2026/draw/reset"),
]


@pytest.mark.parametrize("method,path", [r for r in ROUTES if "/admin" not in r[1]])
def test_participant_routes_reject_anonymous(load, method, path):
    _, app = load(rejecting_auth_module())
    assert getattr(app.test_client(), method)(path).status_code == 401


@pytest.mark.parametrize("method,path", [r for r in ROUTES if "/admin" in r[1]])
def test_admin_routes_need_the_org_header(load, method, path):
    _, app = load(rejecting_auth_module())
    resp = getattr(app.test_client(), method)(path, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_error_mapping(load):
    _, app = load(passthrough_auth_module())
    resp = app.test_client().post("/api/giveaways/fall-2026/entry", json={"all_prizes": True}, headers=HEADERS)
    assert resp.status_code == 409
    assert resp.get_json() == {"error": "giveaway_closed", "message": "Entries for this giveaway are closed."}


def test_actor_and_identity_are_passed(load):
    _, app = load(passthrough_auth_module())
    client = app.test_client()
    assert client.get("/api/giveaways/open", headers=HEADERS).get_json()["who"] == "fake-propel-uuid"
    assert client.post("/api/giveaways/admin", json={"id": "x"}, headers=HEADERS).get_json()["by"] == "fake@example.com"
