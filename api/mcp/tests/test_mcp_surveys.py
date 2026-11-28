"""Tool tests for the survey MCP tools.

The real ``api.surveys.surveys_service`` is stubbed in ``sys.modules``
(only via ``monkeypatch.setitem``) BEFORE importing
``api.mcp.mcp_views``; ``api.mcp.tools._registered`` is reset and the
tool module + views are popped so the tool module re-imports against
the stub (see ``api/mcp/README.md``). These tests prove dispatch, auth,
validation, and error mapping — not Firestore behavior, which the
service layer's own tests cover.
"""
import importlib
import json
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module

VIEWS = "api.mcp.mcp_views"
TOOLS_MODULE = "api.mcp.tools.surveys_tools"
SERVICE = "api.surveys.surveys_service"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}

TOOL_NAMES = (
    "survey_event_responses",
    "survey_event_summary",
    "survey_cross_event_overview",
)


def _make_service_stub():
    """A stand-in surveys_service whose functions record calls and
    answer canned payloads (overridable per test via ``_state``)."""
    stub = types.ModuleType(SERVICE)
    state = {
        "calls": [],
        "returns": {
            "responses": {
                "success": True,
                "responses": [
                    {
                        "id": "evt-1__live__user-1",
                        "event_id": "evt-1",
                        "mode": "live",
                        "role": "hacker",
                        "answers": {"overall_rating": 5},
                    }
                ],
                "count": 1,
            },
            "summary": {
                "success": True,
                "summary": {
                    "count": 1,
                    "by_mode": {"live": 1},
                    "by_role": {"hacker": 1},
                    "overall_rating": {"count": 1, "average": 5.0},
                    "would_return": {"count": 0, "average": None},
                },
            },
            "overview": {
                "success": True,
                "totals": {
                    "responses": 1,
                    "events": 1,
                    "by_mode": {"live": 1},
                    "by_role": {"hacker": 1},
                },
                "events": [{"event_id": "evt-1", "count": 1}],
            },
        },
    }
    stub._state = state

    def get_event_survey_responses(event_id, mode=None):
        state["calls"].append(("responses", event_id, mode))
        return state["returns"]["responses"]

    def get_event_survey_summary(event_id):
        state["calls"].append(("summary", event_id))
        return state["returns"]["summary"]

    def get_cross_event_survey_overview():
        state["calls"].append(("overview",))
        return state["returns"]["overview"]

    stub.get_event_survey_responses = get_event_survey_responses
    stub.get_event_survey_summary = get_event_survey_summary
    stub.get_cross_event_survey_overview = get_cross_event_survey_overview
    return stub


@pytest.fixture
def client_and_stub(monkeypatch):
    from api.mcp import registry, tools as tools_pkg

    stub = _make_service_stub()
    monkeypatch.setitem(sys.modules, SERVICE, stub)
    monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
    # Clear any earlier registration so the re-import registers fresh.
    for name in TOOL_NAMES:
        registry.TOOL_REGISTRY.pop(name, None)
    monkeypatch.setattr(tools_pkg, "_registered", False)
    sys.modules.pop(TOOLS_MODULE, None)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client(), stub, views
    sys.modules.pop(VIEWS, None)
    sys.modules.pop(TOOLS_MODULE, None)
    for name in TOOL_NAMES:
        registry.TOOL_REGISTRY.pop(name, None)


def _call(client, name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params},
        headers=HEADERS,
    )


def _payload(response):
    assert response.status_code == 200
    result = response.json["result"]
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])


def test_survey_event_responses_happy_path(client_and_stub):
    client, stub, _views = client_and_stub
    payload = _payload(
        _call(client, "survey_event_responses", {"event_id": "evt-1", "mode": "live"})
    )
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["responses"][0]["event_id"] == "evt-1"
    assert stub._state["calls"] == [("responses", "evt-1", "live")]


def test_survey_event_responses_without_mode_calls_service_unfiltered(client_and_stub):
    client, stub, _views = client_and_stub
    payload = _payload(_call(client, "survey_event_responses", {"event_id": "evt-1"}))
    assert payload["success"] is True
    assert stub._state["calls"] == [("responses", "evt-1", None)]


def test_survey_event_summary_happy_path(client_and_stub):
    client, stub, _views = client_and_stub
    payload = _payload(_call(client, "survey_event_summary", {"event_id": "evt-1"}))
    assert payload["summary"]["count"] == 1
    assert payload["summary"]["overall_rating"]["average"] == 5.0
    assert stub._state["calls"] == [("summary", "evt-1")]


def test_survey_cross_event_overview_happy_path(client_and_stub):
    client, stub, _views = client_and_stub
    payload = _payload(_call(client, "survey_cross_event_overview", {}))
    assert payload["totals"]["responses"] == 1
    assert payload["events"][0]["event_id"] == "evt-1"
    assert stub._state["calls"] == [("overview",)]


def test_survey_event_responses_rejects_invalid_mode(client_and_stub):
    client, stub, _views = client_and_stub
    response = _call(
        client, "survey_event_responses", {"event_id": "evt-1", "mode": "upcoming"}
    )
    assert response.status_code == 200
    assert response.json["error"]["code"] == -32602
    assert stub._state["calls"] == []


def test_survey_tools_reject_missing_event_id(client_and_stub):
    client, stub, _views = client_and_stub
    for name in ("survey_event_responses", "survey_event_summary"):
        response = _call(client, name, {})
        assert response.json["error"]["code"] == -32602, name
    assert stub._state["calls"] == []


def test_service_error_passes_through_as_is_error(client_and_stub):
    client, stub, _views = client_and_stub
    stub._state["returns"]["summary"] = {
        "success": False,
        "error": "event_not_found",
        "message": "No such event",
    }
    response = _call(client, "survey_event_summary", {"event_id": "evt-nope"})
    assert response.status_code == 200
    result = response.json["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "event_not_found"


def test_non_admin_caller_is_denied(client_and_stub, monkeypatch):
    client, stub, views = client_and_stub
    # The endpoint gate lets the request through; the dispatcher must
    # still refuse a caller with none of the tool's permissions.
    monkeypatch.setattr(views, "user_has_any_permission", lambda user, perms: False)
    response = _call(client, "survey_event_summary", {"event_id": "evt-1"})
    assert response.status_code == 200
    assert response.json["error"]["code"] == -32001
    assert stub._state["calls"] == []


def test_tools_list_includes_the_three_survey_tools(client_and_stub):
    client, _stub, _views = client_and_stub
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    names = [entry["name"] for entry in response.json["result"]["tools"]]
    assert names == sorted(TOOL_NAMES)
