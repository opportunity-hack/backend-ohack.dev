"""Calendar tools (``meet_*``): admin wrappers over ``api.meet.meet_service``.

The handlers are thin: they build the service call's arguments from the
tool arguments, resolve caller identity from the verified token (the
``admin`` argument the dispatcher builds), call the existing service
function, and return its ``(payload, status)`` tuple unchanged. No
business logic lives here, and no tool takes identity from its arguments.

Polls (``meet_polls``, doc id = code): ``code``, ``title``,
``description``, ``duration_minutes``, ``slots`` ``[{id, start}]`` (UTC
``YYYY-MM-DDTHH:MM:SSZ``), ``status`` ``open|closed|finalized``,
``final_slot_id``, ``calendar_event_id``, ``meet_link``,
``calendar_html_link``, ``invited_emails``. Slot starts must carry a
timezone offset; the service refuses naive values.

Finalizing creates the Google Calendar event FIRST (deterministic event
id, so a retry reuses it) and only then marks the poll finalized; a
calendar failure leaves the poll open (``calendar_failed`` 502,
``calendar_not_configured`` 503), and re-finalizing is 409
``already_finalized``. There is no cancel tool: closing a poll is
``meet_update_poll`` with ``status: "closed"``.
"""
from typing import Any, Dict, List, Optional, Tuple

from api.meet import meet_service
from api.mcp.registry import VOLUNTEER_ADMIN, register_tool

_PERMISSIONS = (VOLUNTEER_ADMIN,)

_OBJECT = {"type": "object"}
_STATUS_ENUM = ["open", "closed", "finalized"]
_CONFIRM_PROPERTY = {"confirm": {"type": "boolean"}}


def _handler_result(payload, status):
    # type: (Dict[str, Any], int) -> Tuple[Dict[str, Any], int]
    return payload, status


def _meet_list_polls(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """List admin polls (``admin_list_polls``), optionally filtered by status.

    The service has no status filter or pagination, so a ``status``
    argument filters the returned list client-side here.
    """
    payload, status = meet_service.admin_list_polls()
    wanted = arguments.get("status")
    if wanted is not None and isinstance(payload.get("data"), list):
        payload = dict(payload)
        payload["data"] = [
            poll for poll in payload["data"] if poll.get("status") == wanted
        ]
    return _handler_result(payload, status)


register_tool(
    "meet_list_polls",
    "List meeting scheduling polls with vote counts, newest first. "
    "Optionally filter by poll status (open, closed, finalized). "
    "Also reports whether Google Calendar is configured.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _STATUS_ENUM},
        },
        "additionalProperties": False,
    },
    _PERMISSIONS,
)(_meet_list_polls)


def _meet_get_poll(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Admin detail for one poll (``admin_get_poll``): poll + votes."""
    return meet_service.admin_get_poll(arguments["code"])


register_tool(
    "meet_get_poll",
    "Get one meeting scheduling poll by code, with every response "
    "including respondent emails and the vote tally.",
    {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
)(_meet_get_poll)


def _meet_create_poll(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Create a poll (``admin_create_poll``). Caller identity from the token."""
    data = {
        "title": arguments["title"],
        "slots": arguments["slots"],
    }  # type: Dict[str, Any]
    for key in ("code", "description", "durationMinutes"):
        if key in arguments:
            data[key] = arguments[key]
    return meet_service.admin_create_poll(data, admin)


register_tool(
    "meet_create_poll",
    "Create a meeting scheduling poll. Title and at least one candidate "
    "slot are required; each slot start must carry a timezone offset "
    "(naive times are refused). Optional: code (auto-generated when "
    "omitted), description, durationMinutes (5-480, default 30).",
    {
        "type": "object",
        "properties": dict(
            {
                "title": {"type": "string"},
                "slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string"},
                            "id": {"type": "string"},
                        },
                        "required": ["start"],
                    },
                },
                "code": {"type": "string"},
                "description": {"type": "string"},
                "durationMinutes": {"type": "integer"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["title", "slots", "confirm"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)(_meet_create_poll)


def _meet_update_poll(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Edit a poll (``admin_update_poll``). ``status: closed`` closes it;
    a finalized poll is read-only (409 already_finalized)."""
    data = {}  # type: Dict[str, Any]
    for key in ("title", "description", "durationMinutes", "slots", "status"):
        if key in arguments:
            data[key] = arguments[key]
    return meet_service.admin_update_poll(arguments["code"], data)


register_tool(
    "meet_update_poll",
    "Edit a meeting scheduling poll: title, description, durationMinutes, "
    "candidate slots, or status (open or closed; closing a poll uses "
    "status closed). A finalized poll cannot be edited.",
    {
        "type": "object",
        "properties": dict(
            {
                "code": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "durationMinutes": {"type": "integer"},
                "slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string"},
                            "id": {"type": "string"},
                        },
                        "required": ["start"],
                    },
                },
                "status": {"type": "string", "enum": ["open", "closed"]},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["code", "confirm"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)(_meet_update_poll)


def _meet_delete_poll(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Delete a poll and its responses (``admin_delete_poll``).

    Does NOT cancel a calendar event that was already sent; do that in
    Google Calendar.
    """
    return meet_service.admin_delete_poll(arguments["code"])


register_tool(
    "meet_delete_poll",
    "Delete a meeting scheduling poll and all its responses. This does "
    "not cancel a Google Calendar event that was already sent.",
    {
        "type": "object",
        "properties": dict(
            {"code": {"type": "string"}},
            **_CONFIRM_PROPERTY
        ),
        "required": ["code", "confirm"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)(_meet_delete_poll)


def _meet_delete_vote(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Remove one response (``admin_delete_vote``)."""
    return meet_service.admin_delete_vote(arguments["code"], arguments["voteId"])


register_tool(
    "meet_delete_vote",
    "Delete one response to a meeting scheduling poll, by poll code and "
    "response id.",
    {
        "type": "object",
        "properties": dict(
            {
                "code": {"type": "string"},
                "voteId": {"type": "string"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["code", "voteId", "confirm"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)(_meet_delete_vote)


def _meet_finalize_poll(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    """Pick the final time (``admin_finalize_poll``).

    With ``sendInvites`` (default true) the event is created on the
    organizer's Google Calendar with a Meet link and Google emails every
    attendee. Caller identity from the token.
    """
    data = {"slotId": arguments["slotId"]}  # type: Dict[str, Any]
    for key in ("sendInvites", "voteIds", "extraEmails"):
        if key in arguments:
            data[key] = arguments[key]
    return meet_service.admin_finalize_poll(arguments["code"], data, admin)


register_tool(
    "meet_finalize_poll",
    "Finalize a meeting scheduling poll: pick the final slot, create the "
    "Google Calendar event with a Meet link, and invite the respondents. "
    "Optional: sendInvites (default true; false records the time without "
    "touching Google Calendar), voteIds (who to invite; default every "
    "respondent), extraEmails. Re-finalizing is refused (409).",
    {
        "type": "object",
        "properties": dict(
            {
                "code": {"type": "string"},
                "slotId": {"type": "string"},
                "sendInvites": {"type": "boolean"},
                "voteIds": {"type": "array", "items": {"type": "string"}},
                "extraEmails": {"type": "array", "items": {"type": "string"}},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["code", "slotId", "confirm"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)(_meet_finalize_poll)
