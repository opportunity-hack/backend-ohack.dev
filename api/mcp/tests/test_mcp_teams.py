"""Tests for the team admin MCP tools (``team_*``).

The service layer is stubbed in ``sys.modules`` (only via
``monkeypatch.setitem``) BEFORE importing ``api.mcp.mcp_views``, and
``api.mcp.tools._registered`` is reset + the tool module popped so it
re-imports against the stub — the pattern ``api/mcp/README.md``
documents. The stubs record every call so the tests prove the handlers
pass the right arguments (and the token identity, never a tool
argument) through to the service. Auth is the shared passthrough stub,
except the non-admin test, which swaps in a stub whose
``user_has_any_permission`` denies.
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
from api.mcp import registry

HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}

TEAM_TOOLS = [
    "team_list_queued",
    "team_approve",
    "team_edit",
    "team_add_member",
    "team_remove_member",
    "team_remove",
    "team_message",
    "team_gateway_key_statuses",
    "team_rotate_gateway_key",
    "team_provision_gateway_key",
]

CALLS = {
    "get_queued_teams": [],
    "approve_team": [],
    "edit_team": [],
    "add_team_member": [],
    "remove_team_member": [],
    "remove_team": [],
    "send_team_message": [],
    "list_gateway_key_statuses": [],
    "rotate_team_gateway_key": [],
    "provision_team_gateway_key": [],
}

KEY_METADATA = {
    "team_id": "team-1",
    "keys": [
        {"label": "Key 1", "status": "active"},
        {"label": "Key 2", "status": "active"},
    ],
}


def _make_service_stub():
    stub = types.ModuleType("api.teams.teams_service")

    def get_queued_teams():
        CALLS["get_queued_teams"].append(())
        return {"teams": [{"id": "team-1", "name": "Alpha", "status": "IN_REVIEW"}]}

    def approve_team(admin_user_id, body):
        CALLS["approve_team"].append((admin_user_id, body))
        if body.get("teamId") == "team-bad":
            return (
                {
                    "error": "already_approved",
                    "message": "This team was already approved.",
                    "success": False,
                },
                409,
            )
        return {"success": True, "team": {"id": body["teamId"]}, "team_id": body["teamId"]}

    def edit_team(body):
        CALLS["edit_team"].append(body)
        return {
            "message": "Team updated successfully",
            "success": True,
            "team_id": body["id"],
        }

    def add_team_member(team_id, user_id):
        CALLS["add_team_member"].append((team_id, user_id))
        return {"message": "User added", "success": True, "team_id": team_id}

    def remove_team_member(team_id, user_id):
        CALLS["remove_team_member"].append((team_id, user_id))
        return {"message": "User removed", "success": True, "team_id": team_id}

    def remove_team(team_id):
        CALLS["remove_team"].append(team_id)
        return {"message": "Team removed", "success": True, "team_id": team_id}

    def send_team_message(admin_user, teamid, body):
        CALLS["send_team_message"].append((admin_user, teamid, body))
        return {
            "success": True,
            "team_id": teamid,
            "delivery": {"slack": True},
            "message": "Message sent via slack.",
        }

    stub.get_queued_teams = get_queued_teams
    stub.approve_team = approve_team
    stub.edit_team = edit_team
    stub.add_team_member = add_team_member
    stub.remove_team_member = remove_team_member
    stub.remove_team = remove_team
    stub.send_team_message = send_team_message
    return stub


def _make_gateway_keys_stub():
    stub = types.ModuleType("api.teams.gateway_keys")
    stub.MAX_STATUS_BATCH = 200

    def list_gateway_key_statuses(team_ids):
        CALLS["list_gateway_key_statuses"].append(list(team_ids))
        return {"team-1": {"keys": [{"label": "Key 1", "status": "active"}]}}

    def rotate_team_gateway_key(team_id, key):
        CALLS["rotate_team_gateway_key"].append((team_id, key))
        return dict(KEY_METADATA)

    def provision_team_gateway_key(team_id):
        CALLS["provision_team_gateway_key"].append(team_id)
        return dict(KEY_METADATA)

    stub.list_gateway_key_statuses = list_gateway_key_statuses
    stub.rotate_team_gateway_key = rotate_team_gateway_key
    stub.provision_team_gateway_key = provision_team_gateway_key
    return stub


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setitem(sys.modules, "api.teams.teams_service", _make_service_stub())
    mp.setitem(sys.modules, "api.teams.gateway_keys", _make_gateway_keys_stub())
    mp.setitem(sys.modules, "common.auth", passthrough_auth_module())
    import api.mcp.tools as tools_pkg

    mp.setattr(tools_pkg, "_registered", False)
    # Re-importing the tool module re-runs @register_tool, which raises
    # "already registered" when an earlier test file loaded the real
    # module. Start from an empty registry and restore the original on
    # teardown so other test files keep their tools.
    saved_registry = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    sys.modules.pop("api.mcp.tools.teams", None)
    sys.modules.pop("api.mcp.mcp_views", None)
    views = importlib.import_module("api.mcp.mcp_views")
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client()
    mp.undo()
    sys.modules.pop("api.mcp.tools.teams", None)
    sys.modules.pop("api.mcp.mcp_views", None)
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved_registry)


@pytest.fixture(autouse=True)
def _clear_calls():
    for recorded in CALLS.values():
        recorded.clear()
    yield


def _call(client, name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": params,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    return response.json


def _payload(response):
    assert "result" in response, response
    assert response["result"]["isError"] is False, response
    return json.loads(response["result"]["content"][0]["text"])


def test_tools_list_includes_all_ten_team_tools(client):
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    names = [entry["name"] for entry in response.json["result"]["tools"]]
    for tool in TEAM_TOOLS:
        assert tool in names


def test_list_queued_happy_path(client):
    payload = _payload(_call(client, "team_list_queued", {}))
    assert payload == {
        "teams": [{"id": "team-1", "name": "Alpha", "status": "IN_REVIEW"}]
    }
    assert CALLS["get_queued_teams"] == [()]


def test_approve_with_confirm_passes_token_identity(client):
    payload = _payload(
        _call(
            client,
            "team_approve",
            {
                "team_id": "team-1",
                "nonprofit_id": "npo-1",
                "problem_statement_id": "ps-1",
                "confirm": True,
            },
        )
    )
    assert payload["success"] is True
    assert CALLS["approve_team"] == [
        (
            "fake-propel-uuid",
            {
                "teamId": "team-1",
                "nonprofitId": "npo-1",
                "problemStatementId": "ps-1",
            },
        )
    ]


def test_edit_with_confirm_forwards_only_passed_fields(client):
    payload = _payload(
        _call(
            client,
            "team_edit",
            {
                "team_id": "team-1",
                "name": "New name",
                "slack_channel": "new-channel",
                "nonprofit_rankings": [{"nonprofit_id": "npo-1", "rank": 1}],
                "confirm": True,
            },
        )
    )
    assert payload["success"] is True
    assert CALLS["edit_team"] == [
        {
            "id": "team-1",
            "name": "New name",
            "slackChannel": "new-channel",
            "nonprofitRankings": [{"nonprofit_id": "npo-1", "rank": 1}],
        }
    ]


def test_approve_refused_without_confirm(client):
    response = _call(
        client,
        "team_approve",
        {"team_id": "team-1", "nonprofit_id": "npo-1"},
    )
    assert response["error"]["code"] == -32602
    assert CALLS["approve_team"] == []


def test_add_member_with_confirm(client):
    payload = _payload(
        _call(
            client,
            "team_add_member",
            {"team_id": "team-1", "user_id": "user-9", "confirm": True},
        )
    )
    assert payload["success"] is True
    assert CALLS["add_team_member"] == [("team-1", "user-9")]


def test_remove_member_with_confirm(client):
    payload = _payload(
        _call(
            client,
            "team_remove_member",
            {"team_id": "team-1", "user_id": "user-9", "confirm": True},
        )
    )
    assert payload["success"] is True
    assert CALLS["remove_team_member"] == [("team-1", "user-9")]


def test_remove_team_with_confirm(client):
    payload = _payload(
        _call(client, "team_remove", {"team_id": "team-1", "confirm": True})
    )
    assert payload["success"] is True
    assert CALLS["remove_team"] == ["team-1"]


def test_message_with_confirm(client):
    payload = _payload(
        _call(
            client,
            "team_message",
            {
                "team_id": "team-1",
                "message": "Hello team",
                "channels": {"slack": True, "email": True},
                "confirm": True,
            },
        )
    )
    assert payload["success"] is True
    assert len(CALLS["send_team_message"]) == 1
    admin_user, teamid, body = CALLS["send_team_message"][0]
    assert admin_user.user_id == "fake-propel-uuid"
    assert admin_user.email == "fake@example.com"
    assert teamid == "team-1"
    assert body == {
        "message": "Hello team",
        "channels": {"slack": True, "email": True},
    }


def test_gateway_key_statuses_happy_path(client):
    payload = _payload(
        _call(client, "team_gateway_key_statuses", {"team_ids": ["team-1"]})
    )
    assert payload == {
        "keys": {"team-1": {"keys": [{"label": "Key 1", "status": "active"}]}}
    }
    assert CALLS["list_gateway_key_statuses"] == [["team-1"]]


def test_gateway_key_statuses_rejects_empty_and_too_many(client):
    response = _call(client, "team_gateway_key_statuses", {"team_ids": []})
    assert response["result"]["isError"] is True
    assert json.loads(response["result"]["content"][0]["text"])["error"] == (
        "team_ids_required"
    )
    response = _call(
        client,
        "team_gateway_key_statuses",
        {"team_ids": ["team-%d" % n for n in range(201)]},
    )
    assert response["result"]["isError"] is True
    assert json.loads(response["result"]["content"][0]["text"])["error"] == (
        "too_many_team_ids"
    )


def test_rotate_gateway_key_with_confirm(client):
    payload = _payload(
        _call(
            client,
            "team_rotate_gateway_key",
            {"team_id": "team-1", "key": "key2", "confirm": True},
        )
    )
    assert payload == KEY_METADATA
    assert CALLS["rotate_team_gateway_key"] == [("team-1", "key2")]


def test_provision_gateway_key_with_confirm(client):
    payload = _payload(
        _call(
            client,
            "team_provision_gateway_key",
            {"team_id": "team-1", "confirm": True},
        )
    )
    assert payload == KEY_METADATA
    assert CALLS["provision_team_gateway_key"] == ["team-1"]


def test_service_error_passes_through_as_is_error(client):
    response = _call(
        client,
        "team_approve",
        {"team_id": "team-bad", "nonprofit_id": "npo-1", "confirm": True},
    )
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "already_approved"


def test_non_admin_caller_is_denied(monkeypatch):
    auth_stub = passthrough_auth_module()
    auth_stub.user_has_any_permission = lambda user, perms: False
    monkeypatch.setitem(sys.modules, "common.auth", auth_stub)
    sys.modules.pop("api.mcp.mcp_views", None)
    views = importlib.import_module("api.mcp.mcp_views")
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    response = app.test_client().post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "team_list_queued", "arguments": {}},
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json["error"]["code"] == -32001


def test_gateway_key_statuses_drops_non_string_and_blank_ids(client):
    # Only real ids reach the service, like the REST route's ?team_ids= split.
    _call(client, "team_gateway_key_statuses", {"team_ids": [" team-1 ", "", 7]})
    assert CALLS["list_gateway_key_statuses"] == [["team-1"]]
    response = _call(client, "team_gateway_key_statuses", {"team_ids": ["", None]})
    assert "team_ids_required" in response["result"]["content"][0]["text"]
