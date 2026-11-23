"""Tool registry for the admin MCP server.

One registry, ``TOOL_REGISTRY``: tool name -> ``ToolDef``. Tool modules
in ``api/mcp/tools/`` register themselves at import time with the
``@register_tool`` decorator; ``api.mcp.tools.register_all`` imports every
module in that package, so a new tool module needs no edit here.

A handler takes ``(arguments, admin)`` and returns the service layer's
``(payload, http_status)`` tuple, exactly like the REST views do. ``admin``
is the caller identity from the verified PropelAuth token
(``{"propel_id", "email", "name"}``) — handlers must never take identity
from tool arguments.
"""
import copy
from typing import Any, Callable, Dict, List, Optional, Tuple

# PropelAuth permission strings used by the MCP tools.
VOLUNTEER_ADMIN = "volunteer.admin"
CHECKIN_ADMIN = "checkin.admin"
# The endpoint gate: desk helpers may reach the check-in tools.
CHECKIN_PERMISSIONS = (VOLUNTEER_ADMIN, CHECKIN_ADMIN)


class ToolDef(object):
    """One MCP tool: its schema, who may call it, and its handler."""

    def __init__(
        self,
        name,  # type: str
        description,  # type: str
        input_schema,  # type: Dict[str, Any]
        permissions,  # type: Tuple[str, ...]
        handler,  # type: Callable[[Dict[str, Any], Dict[str, Any]], Tuple[Dict[str, Any], int]]
        requires_confirm=False,  # type: bool
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.permissions = permissions
        self.handler = handler
        self.requires_confirm = requires_confirm

    def to_list_entry(self):
        # type: () -> Dict[str, Any]
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


TOOL_REGISTRY = {}  # type: Dict[str, ToolDef]

_CONFIRM_SPEC = {
    "type": "boolean",
    "description": "Must be true: this tool changes state.",
}


def _with_confirm(input_schema):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """A copy of ``input_schema`` that declares ``confirm`` as a required
    boolean. Every ``requires_confirm`` tool gets this, so ``tools/list``
    always tells the client about the gate the dispatcher enforces, even
    when a tool module forgot to declare or require it."""
    schema = copy.deepcopy(input_schema or {"type": "object"})
    schema.setdefault("properties", {}).setdefault("confirm", dict(_CONFIRM_SPEC))
    required = list(schema.get("required") or [])
    if "confirm" not in required:
        required.append("confirm")
    schema["required"] = required
    return schema


def register_tool(
    name,  # type: str
    description,  # type: str
    input_schema,  # type: Dict[str, Any]
    permissions,  # type: Tuple[str, ...]
    requires_confirm=False,  # type: bool
):
    # type: (...) -> Callable[[Callable], Callable]
    """Decorator: register a handler as an MCP tool.

    ``permissions`` is an any-of list: the caller needs at least one.
    ``requires_confirm`` marks a side-effecting tool: the dispatcher
    refuses the call unless the arguments carry ``confirm: true``.
    """

    def decorator(handler):
        # type: (Callable) -> Callable
        if name in TOOL_REGISTRY:
            raise ValueError("MCP tool %r is already registered" % name)
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            input_schema=(
                _with_confirm(input_schema) if requires_confirm else input_schema
            ),
            permissions=permissions,
            handler=handler,
            requires_confirm=requires_confirm,
        )
        return handler

    return decorator


def get_tool(name):
    # type: (str) -> Optional[ToolDef]
    return TOOL_REGISTRY.get(name)


def list_tools():
    # type: () -> List[ToolDef]
    return [TOOL_REGISTRY[name] for name in sorted(TOOL_REGISTRY)]
