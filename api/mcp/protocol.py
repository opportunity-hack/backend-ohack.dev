"""Hand-rolled JSON-RPC 2.0 dispatcher for the admin MCP server.

The backend runs Python 3.9 locally, so the official MCP Python SDK
(3.10+) is not a runtime dependency. This dispatcher covers the tools-only
surface v1 needs: ``initialize``, ``ping``, ``tools/list``, ``tools/call``,
plus ``notifications/*`` no-ops. Stateless: no sessions, no
``Mcp-Session-Id``, no required ``initialize`` handshake (the method is
answered for older clients but never required).

Error codes (JSON-RPC 2.0): ``-32700`` parse error, ``-32600`` invalid
request, ``-32601`` method not found, ``-32602`` invalid params,
``-32603`` internal error, ``-32001`` forbidden (the caller lacks every
permission the tool requires). Tool-execution failures are NOT JSON-RPC
errors: the service layer's ``{error, message}`` payload passes through
as an MCP ``isError`` result, so admin clients see the same codes as
REST (``self_approval`` 403, ``already_approved`` 409, ...).

``dispatch`` is pure: it takes the parsed request body, the verified
``auth_user`` (or ``None``), and a ``has_permission(user, permissions)``
callable, so tests exercise it without Flask or PropelAuth.
"""
import json
from typing import Any, Callable, Dict, Optional, Tuple

from api.mcp import registry

PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "ohack-admin-mcp"
SERVER_VERSION = "0.1.0"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603
_FORBIDDEN = -32001


def _error_response(code, message, request_id):
    # type: (int, str, Any) -> Dict[str, Any]
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result_response(result, request_id):
    # type: (Any, Any) -> Dict[str, Any]
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _is_notification(body):
    # type: (Dict[str, Any]) -> bool
    return "id" not in body


def _type_name(value):
    # type: (Any) -> str
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _check_type(value, expected):
    # type: (Any, str) -> bool
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate_arguments(tool, arguments):
    # type: (registry.ToolDef, Dict[str, Any]) -> Optional[str]
    """Validate ``arguments`` against the tool's input schema.

    Returns an error message for the first problem, else None. Only the
    schema's required/type/enum rules are checked here; business rules
    stay in the service layer.
    """
    schema = tool.input_schema or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    for field in required:
        if field not in arguments or arguments[field] is None:
            return "Missing required argument: %s" % field
    for field, value in arguments.items():
        spec = properties.get(field)
        if spec is None:
            continue
        expected = spec.get("type")
        if expected and not _check_type(value, expected):
            return "Argument %s must be %s, got %s" % (
                field,
                expected,
                _type_name(value),
            )
        choices = spec.get("enum")
        if choices is not None and value not in choices:
            return "Argument %s must be one of %s" % (field, ", ".join(
                str(choice) for choice in choices
            ))
    if schema.get("additionalProperties") is False:
        unknown = [f for f in arguments if f not in properties]
        if unknown:
            return "Unknown argument: %s" % unknown[0]
    return None


def _admin_from_user(user):
    # type: (Any) -> Dict[str, Any]
    """Caller identity from the verified token, never from arguments."""
    if user is None:
        return {}
    try:
        return {
            "propel_id": getattr(user, "user_id", None),
            "email": getattr(user, "email", None),
            "name": getattr(user, "name", None),
        }
    except Exception:
        return {}


def _tool_result(payload, status):
    # type: (Any, int) -> Dict[str, Any]
    """Map a service ``(payload, status)`` to an MCP tool result.

    Most services return a dict, but some return a bare list (e.g. a
    roster). A bare payload is a success unless the status says otherwise;
    only dict payloads can carry the ``success`` / ``error`` markers.
    """
    if isinstance(payload, dict):
        is_error = status >= 400 or payload.get("success") is False or "error" in payload
    else:
        is_error = status >= 400
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": bool(is_error),
    }


def _handle_initialize(body, request_id):
    # type: (Dict[str, Any], Any) -> Dict[str, Any]
    return _result_response(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
        request_id,
    )


def _handle_tools_list(user, has_permission, request_id):
    # type: (Any, Callable, Any) -> Dict[str, Any]
    tools = [
        tool.to_list_entry()
        for tool in registry.list_tools()
        if has_permission(user, tool.permissions)
    ]
    return _result_response({"tools": tools}, request_id)


def _handle_tools_call(body, user, has_permission, request_id):
    # type: (Dict[str, Any], Any, Callable, Any) -> Dict[str, Any]
    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error_response(
            _INVALID_PARAMS, "tools/call params must be an object", request_id
        )
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _error_response(
            _INVALID_PARAMS, "tools/call requires a tool name", request_id
        )
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _error_response(
            _INVALID_PARAMS, "tools/call arguments must be an object", request_id
        )

    tool = registry.get_tool(name)
    if tool is None:
        return _error_response(
            _METHOD_NOT_FOUND, "Unknown tool: %s" % name, request_id
        )
    if not has_permission(user, tool.permissions):
        return _error_response(
            _FORBIDDEN,
            "Forbidden: this tool requires %s" % " or ".join(tool.permissions),
            request_id,
        )
    if tool.requires_confirm and arguments.get("confirm") is not True:
        return _error_response(
            _INVALID_PARAMS,
            "This tool changes state; pass confirm: true to run it",
            request_id,
        )
    problem = _validate_arguments(tool, arguments)
    if problem:
        return _error_response(_INVALID_PARAMS, problem, request_id)

    try:
        payload, status = tool.handler(arguments, _admin_from_user(user))
    except Exception:
        return _error_response(
            _INTERNAL_ERROR, "The tool failed unexpectedly", request_id
        )
    return _result_response(_tool_result(payload, status), request_id)


def dispatch(body, user, has_permission):
    # type: (Any, Any, Callable) -> Tuple[Optional[Dict[str, Any]], int]
    """Dispatch one parsed JSON-RPC request.

    Returns ``(response, http_status)``. ``response`` is None for
    notifications, which get a 202 with no body. ``body`` is None when
    the HTTP layer could not parse the JSON at all.
    """
    if body is None:
        return _error_response(_PARSE_ERROR, "Parse error", None), 200
    if not isinstance(body, dict):
        return _error_response(_INVALID_REQUEST, "Request must be an object", None), 200
    if "jsonrpc" in body and body.get("jsonrpc") != "2.0":
        return _error_response(
            _INVALID_REQUEST, "jsonrpc must be \"2.0\"", body.get("id")
        ), 200
    method = body.get("method")
    if not isinstance(method, str) or not method:
        return _error_response(
            _INVALID_REQUEST, "Request needs a method", body.get("id")
        ), 200
    request_id = body.get("id")

    if method.startswith("notifications/"):
        # No-ops: the client tells us something, we answer nothing.
        return None, 202
    if _is_notification(body):
        # A notification for any other method is still answered with
        # nothing; there is no request id to respond to.
        return None, 202
    if user is None:
        return _error_response(_FORBIDDEN, "Unauthorized", request_id), 200
    if method == "initialize":
        return _handle_initialize(body, request_id), 200
    if method == "ping":
        return _result_response({}, request_id), 200
    if method == "tools/list":
        return _handle_tools_list(user, has_permission, request_id), 200
    if method == "tools/call":
        return _handle_tools_call(body, user, has_permission, request_id), 200
    return _error_response(
        _METHOD_NOT_FOUND, "Unknown method: %s" % method, request_id
    ), 200
