"""Protocol tests for the MCP dispatcher (pure function, no Flask)."""
import json
import types

import pytest

from api.mcp import protocol, registry

USER = types.SimpleNamespace(
    user_id="fake-propel-uuid", email="fake@example.com", name="Fake Admin"
)


def _allow_all(user, permissions):
    return True


def _deny_all(user, permissions):
    return False


def _only_checkin(user, permissions):
    return "checkin.admin" in permissions


@pytest.fixture
def tools():
    saved = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()

    def echo(arguments, admin):
        return {"echo": arguments, "by": admin.get("propel_id")}, 200

    def danger(arguments, admin):
        return {"success": True}, 200

    def desk(arguments, admin):
        return {"desk": True}, 200

    def broken(arguments, admin):
        raise RuntimeError("boom")

    def service_error(arguments, admin):
        return {
            "success": False,
            "error": "already_approved",
            "message": "Already approved",
        }, 409

    def bare_list(arguments, admin):
        return [{"id": "a"}, {"id": "b"}], 200

    def bare_list_error(arguments, admin):
        return [{"id": "a"}], 404

    registry.register_tool(
        "test_echo",
        "Echo the arguments back",
        {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "count": {"type": "integer"},
                "level": {"type": "string", "enum": ["low", "high"]},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        ("volunteer.admin",),
    )(echo)
    registry.register_tool(
        "test_danger",
        "A side-effecting test tool",
        {"type": "object", "properties": {}},
        ("volunteer.admin",),
        requires_confirm=True,
    )(danger)
    registry.register_tool(
        "test_desk",
        "A desk-helper test tool",
        {"type": "object", "properties": {}},
        ("volunteer.admin", "checkin.admin"),
    )(desk)
    registry.register_tool(
        "test_broken",
        "A tool whose handler raises",
        {"type": "object", "properties": {}},
        ("volunteer.admin",),
    )(broken)
    registry.register_tool(
        "test_service_error",
        "A tool whose service answers an error",
        {"type": "object", "properties": {}},
        ("volunteer.admin",),
    )(service_error)
    registry.register_tool(
        "test_bare_list",
        "A tool whose service returns a bare list",
        {"type": "object", "properties": {}},
        ("volunteer.admin",),
    )(bare_list)
    registry.register_tool(
        "test_bare_list_error",
        "A tool whose service returns a bare list with an error status",
        {"type": "object", "properties": {}},
        ("volunteer.admin",),
    )(bare_list_error)
    yield
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved)


def _call(name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": params,
    }


def test_initialize_returns_capabilities_and_server_info(tools):
    response, status = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        USER,
        _allow_all,
    )
    assert status == 200
    result = response["result"]
    assert result["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == protocol.SERVER_NAME


def test_ping_returns_empty_result(tools):
    response, status = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 7, "method": "ping"}, USER, _allow_all
    )
    assert status == 200 and response["result"] == {} and response["id"] == 7


def test_tools_list_is_filtered_by_caller_permissions(tools):
    response, _ = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        USER,
        _only_checkin,
    )
    names = [entry["name"] for entry in response["result"]["tools"]]
    assert names == ["test_desk"]


def test_tools_list_empty_when_nothing_registered():
    registry.TOOL_REGISTRY.clear()
    try:
        response, _ = protocol.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            USER,
            _allow_all,
        )
        assert response["result"] == {"tools": []}
    finally:
        registry.TOOL_REGISTRY.clear()


def test_stateless_request_needs_no_prior_initialize(tools):
    # No initialize was sent; tools/call still works.
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi"}), USER, _allow_all
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["echo"] == {"message": "hi"}
    assert response["result"]["isError"] is False


def test_call_passes_caller_identity_from_the_token(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi"}), USER, _allow_all
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["by"] == "fake-propel-uuid"


def test_call_unknown_tool_is_method_not_found(tools):
    response, _ = protocol.dispatch(
        _call("test_nope", {}), USER, _allow_all
    )
    assert response["error"]["code"] == -32601


def test_call_rejects_missing_required_argument(tools):
    response, _ = protocol.dispatch(_call("test_echo", {}), USER, _allow_all)
    assert response["error"]["code"] == -32602
    assert "message" in response["error"]["message"]


def test_call_rejects_wrong_argument_type(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi", "count": "three"}), USER, _allow_all
    )
    assert response["error"]["code"] == -32602
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi", "count": True}), USER, _allow_all
    )
    assert response["error"]["code"] == -32602


def test_call_rejects_value_outside_enum(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi", "level": "urgent"}),
        USER,
        _allow_all,
    )
    assert response["error"]["code"] == -32602


def test_call_rejects_unknown_argument_when_schema_is_closed(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi", "surprise": 1}), USER, _allow_all
    )
    assert response["error"]["code"] == -32602


def test_call_requires_confirm_on_side_effecting_tools(tools):
    response, _ = protocol.dispatch(_call("test_danger", {}), USER, _allow_all)
    assert response["error"]["code"] == -32602
    response, _ = protocol.dispatch(
        _call("test_danger", {"confirm": False}), USER, _allow_all
    )
    assert response["error"]["code"] == -32602
    response, _ = protocol.dispatch(
        _call("test_danger", {"confirm": True}), USER, _allow_all
    )
    assert response["result"]["isError"] is False


def test_call_denied_without_any_required_permission(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi"}), USER, _deny_all
    )
    assert response["error"]["code"] == -32001


def test_call_handler_exception_is_internal_error(tools):
    response, _ = protocol.dispatch(_call("test_broken", {}), USER, _allow_all)
    assert response["error"]["code"] == -32603
    # Details stay in the server log, never in the response.
    assert "boom" not in response["error"]["message"]


def test_call_service_error_passes_through_as_is_error(tools):
    response, _ = protocol.dispatch(
        _call("test_service_error", {}), USER, _allow_all
    )
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "already_approved"


def test_call_bare_list_payload_is_a_success_result(tools):
    # Some services return a bare list (e.g. a roster). The dispatcher
    # must serialize it, not crash on payload.get().
    response, _ = protocol.dispatch(_call("test_bare_list", {}), USER, _allow_all)
    result = response["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == [{"id": "a"}, {"id": "b"}]


def test_call_bare_list_payload_with_error_status_is_error(tools):
    response, _ = protocol.dispatch(
        _call("test_bare_list_error", {}), USER, _allow_all
    )
    assert response["result"]["isError"] is True


def test_call_rejects_non_object_params_and_arguments(tools):
    response, _ = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]},
        USER,
        _allow_all,
    )
    assert response["error"]["code"] == -32602
    response, _ = protocol.dispatch(
        _call("test_echo", "not-an-object"), USER, _allow_all
    )
    assert response["error"]["code"] == -32602


def test_unparsable_body_is_parse_error(tools):
    response, status = protocol.dispatch(None, USER, _allow_all)
    assert status == 200
    assert response["error"]["code"] == -32700
    assert response["id"] is None


def test_non_object_body_is_invalid_request(tools):
    response, _ = protocol.dispatch([1, 2], USER, _allow_all)
    assert response["error"]["code"] == -32600


def test_unknown_method_is_method_not_found(tools):
    response, _ = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        USER,
        _allow_all,
    )
    assert response["error"]["code"] == -32601


def test_wrong_jsonrpc_version_is_invalid_request(tools):
    response, _ = protocol.dispatch(
        {"jsonrpc": "1.0", "id": 1, "method": "ping"}, USER, _allow_all
    )
    assert response["error"]["code"] == -32600


def test_notification_gets_no_response(tools):
    response, status = protocol.dispatch(
        {"jsonrpc": "2.0", "method": "tools/call",
         "params": {"name": "test_echo", "arguments": {"message": "hi"}}},
        USER,
        _allow_all,
    )
    assert response is None and status == 202


def test_notifications_namespace_is_a_noop(tools):
    response, status = protocol.dispatch(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        USER,
        _allow_all,
    )
    assert response is None and status == 202


def test_anonymous_caller_is_forbidden(tools):
    response, _ = protocol.dispatch(
        _call("test_echo", {"message": "hi"}), None, _allow_all
    )
    assert response["error"]["code"] == -32001
