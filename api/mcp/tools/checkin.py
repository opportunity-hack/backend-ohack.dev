"""Check-in desk tools for the admin MCP server.

Thin wrappers over ``api.checkin.checkin_service`` — the same service
behind the ``/api/checkin`` REST routes. Handlers return the service
``(payload, status)`` tuple; identity (``propel_id``) comes from the
verified token via the ``admin`` argument, never from tool arguments.

Permissions — this module is the PR that proves per-tool permissions
in the registry:

- ``checkin_roster``, ``checkin_checked_in``, ``checkin_check_in`` accept
  ``volunteer.admin`` OR ``checkin.admin`` (a desk helper may read the
  roster and check people in).
- ``checkin_reset`` is ``volunteer.admin``-ONLY: a desk helper can never
  undo check-ins. The endpoint gate lets a ``checkin.admin``-only caller
  reach the endpoint; the dispatcher then forbids the reset with -32001.

State-moving tools take a required ``confirm: true`` argument
(``checkin_check_in``, ``checkin_reset``); the dispatcher refuses the
call without it. ``checkin_reset`` also mirrors the REST scope rules:
an empty scope is a 400 ``missing_scope`` and more than
``MAX_RESET_IDS`` ids is a 400 ``too_many_ids``.
"""
from typing import Any, Dict, Tuple

from api.checkin import checkin_service
from api.mcp.registry import (
    CHECKIN_PERMISSIONS,
    VOLUNTEER_ADMIN,
    register_tool,
)

# The service's own allowlist, reused so the schema never drifts from it.
VOLUNTEER_TYPES = checkin_service.VOLUNTEER_TYPES


def _event_and_type_schema():
    # type: () -> Dict[str, Any]
    return {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "type": {"type": "string", "enum": list(VOLUNTEER_TYPES)},
        },
        "required": ["event_id", "type"],
        "additionalProperties": False,
    }


def _roster_result(rows):
    # type: (list) -> Tuple[Dict[str, Any], int]
    """The service returns a bare list; the MCP result must be a dict,
    so wrap it exactly like the REST views do (``data`` + ``count``)."""
    return {"data": rows, "count": len(rows)}, 200


@register_tool(
    "checkin_roster",
    "Lean check-in roster for one event and volunteer type (desk view).",
    _event_and_type_schema(),
    CHECKIN_PERMISSIONS,
)
def checkin_roster(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    rows = checkin_service.get_checkin_roster(
        arguments["event_id"], arguments["type"]
    )
    return _roster_result(rows)


@register_tool(
    "checkin_checked_in",
    "Who is already checked in for one event and volunteer type.",
    _event_and_type_schema(),
    CHECKIN_PERMISSIONS,
)
def checkin_checked_in(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    rows = checkin_service.get_checked_in(arguments["event_id"], arguments["type"])
    return _roster_result(rows)


@register_tool(
    "checkin_check_in",
    "Check one volunteer in for an event. Idempotent: a repeat check-in "
    "keeps the first timestamp.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "type": {"type": "string", "enum": list(VOLUNTEER_TYPES)},
            "volunteer_id": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["event_id", "type", "volunteer_id", "confirm"],
        "additionalProperties": False,
    },
    CHECKIN_PERMISSIONS,
    requires_confirm=True,
)
def checkin_check_in(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    try:
        result = checkin_service.check_in_volunteer(
            arguments["event_id"],
            arguments["type"],
            arguments["volunteer_id"],
            admin.get("propel_id"),
        )
    except checkin_service.CheckInError as e:
        return {"error": e.error, "message": e.message}, e.status
    return result, 200


@register_tool(
    "checkin_reset",
    "Clear check-ins for one event and volunteer type. volunteer.admin "
    "only. Scope is required: volunteer_ids (at most %d) or all=true "
    "(everyone of that type who is checked in)." % checkin_service.MAX_RESET_IDS,
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "type": {"type": "string", "enum": list(VOLUNTEER_TYPES)},
            "volunteer_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "all": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        },
        "required": ["event_id", "type", "confirm"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def checkin_reset(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    # Scope rules mirror the REST view: an empty body must never reset
    # everyone by accident, and the id list is capped.
    ids = arguments.get("volunteer_ids")
    reset_all = arguments.get("all") is True
    if ids is not None:
        if not isinstance(ids, list) or not all(
            isinstance(i, str) and i for i in ids
        ):
            return (
                {
                    "error": "invalid_ids",
                    "message": "volunteer_ids must be a list of ids.",
                },
                400,
            )
        if len(ids) > checkin_service.MAX_RESET_IDS:
            return (
                {
                    "error": "too_many_ids",
                    "message": "Reset at most %s people at a time."
                    % checkin_service.MAX_RESET_IDS,
                },
                400,
            )
        if not ids:
            return (
                {
                    "error": "missing_scope",
                    "message": "Pick at least one person to reset.",
                },
                400,
            )
    elif not reset_all:
        return (
            {
                "error": "missing_scope",
                "message": 'Send {"volunteer_ids": [...]} or {"all": true}.',
            },
            400,
        )
    result = checkin_service.reset_checkins(
        arguments["event_id"],
        arguments["type"],
        admin.get("propel_id"),
        volunteer_ids=None if ids is None else ids,
    )
    return result, 200
