"""Route tests for POST /api/mcp (fixture copied from the meet route
tests: common.auth is stubbed, dispatch through Flask)."""
import importlib
import inspect
import os
import sys

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.mcp.mcp_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
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


def test_post_rejects_anonymous_and_orgless(load):
    client = load(rejecting_auth_module())[1].test_client()
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert client.post("/api/mcp", json=body).status_code == 401
    assert client.post(
        "/api/mcp",
        json=body,
        headers={"Authorization": "Bearer <redacted>"},
    ).status_code == 403
    r = client.post("/api/mcp", json=body, headers=HEADERS)
    assert r.status_code == 200
    assert r.json["result"]["protocolVersion"] == "2026-07-28"


def test_get_is_405(load):
    client = load(passthrough_auth_module())[1].test_client()
    assert client.get("/api/mcp", headers=HEADERS).status_code == 405


def test_malformed_json_is_parse_error(load):
    client = load(passthrough_auth_module())[1].test_client()
    r = client.post(
        "/api/mcp",
        data="{not json",
        headers=HEADERS,
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json["error"]["code"] == -32700


def test_tools_list_returns_registered_tools(load):
    # The core PR had no tool modules, so this asserted an empty list.
    # With the volunteers tools registered, tools/list returns exactly
    # those six tools (sorted by name) for a volunteer.admin caller.
    client = load(passthrough_auth_module())[1].test_client()
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    names = [entry["name"] for entry in r.json["result"]["tools"]]
    assert names == [
        "volunteers_bulk_refund_deposits",
        "volunteers_list_by_event",
        "volunteers_message",
        "volunteers_refund_deposit",
        "volunteers_resend_status",
        "volunteers_select",
    ]


def test_ping_through_the_route(load):
    client = load(passthrough_auth_module())[1].test_client()
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 9, "method": "ping"},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json["result"] == {}
