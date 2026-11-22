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


def test_tools_list_through_the_route(load):
    # Deliberately says nothing about WHICH tools exist: each tool module
    # asserts its own names in its own test file, so adding a module never
    # edits this test. (The empty registry is covered in test_mcp_protocol.)
    client = load(passthrough_auth_module())[1].test_client()
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    tools = r.json["result"]["tools"]
    names = [entry["name"] for entry in tools]
    assert names == sorted(names)
    for entry in tools:
        assert set(entry) == {"name", "description", "inputSchema"}


def test_failed_tool_call_is_audited_as_an_error(load, monkeypatch):
    # A JSON-RPC error (here: unknown tool) has no ``result``; the audit
    # line must still record the call as failed, never as a success.
    views, app = load(passthrough_auth_module())
    audited = []
    monkeypatch.setattr(
        views, "_audit_tool_call", lambda name, caller, is_error: audited.append(
            (name, is_error)
        )
    )
    r = app.test_client().post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "no_such\ntool", "arguments": {}},
        },
        headers=HEADERS,
    )
    assert r.json["error"]["code"] == -32601
    assert audited == [("no_such tool", True)]


def test_ping_through_the_route(load):
    client = load(passthrough_auth_module())[1].test_client()
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 9, "method": "ping"},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json["result"] == {}
