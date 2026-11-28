"""Survey admin read tools for the MCP server.

Thin wrappers over ``api.surveys.surveys_service`` — the same functions
the ``volunteer.admin`` REST routes in ``api/surveys/surveys_views.py``
call. All three tools are read-only (no ``confirm``) and
``volunteer.admin``-only.

Design note: the survey service functions return a bare payload dict,
not the ``(payload, status)`` tuple other services return, so each
handler pairs the dict with 200 — the same wrap the REST views do with
``jsonify(...), 200``. A ``{"success": False, ...}`` payload still maps
to ``isError`` through the dispatcher.
"""
from typing import Any, Dict, Tuple

from api.mcp.registry import VOLUNTEER_ADMIN, register_tool
from api.surveys.surveys_service import (
    get_cross_event_survey_overview,
    get_event_survey_responses,
    get_event_survey_summary,
)

_SURVEY_MODES = ("live", "post")


@register_tool(
    "survey_event_responses",
    "All survey responses for one event (PII-light: ip_address dropped), "
    "newest first. Optional mode filters to live or post responses.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "mode": {"type": "string", "enum": list(_SURVEY_MODES)},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def survey_event_responses(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return (
        get_event_survey_responses(
            arguments["event_id"], arguments.get("mode")
        ),
        200,
    )


@register_tool(
    "survey_event_summary",
    "Light aggregate for one event: response counts by mode and role, "
    "plus averages of overall_rating and would_return.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def survey_event_summary(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return get_event_survey_summary(arguments["event_id"]), 200


@register_tool(
    "survey_cross_event_overview",
    "Cross-event aggregate for the 'Compare events' view: per-event counts, "
    "mode/role breakdowns, and overall_rating / would_return averages, "
    "joined with hackathon metadata. Aggregates only, no PII.",
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def survey_cross_event_overview(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return get_cross_event_survey_overview(), 200
