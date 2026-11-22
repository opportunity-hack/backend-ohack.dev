"""Team admin tools for the MCP server (``team_*``).

Thin wrappers over the existing service layer: ``api.teams.teams_service``
for the queue / approval / roster / messaging surface and
``api.teams.gateway_keys`` for the LiteLLM gateway keys minted at
approval. All ten tools are ``volunteer.admin``-only. The seven
side-effecting tools require ``confirm: true``; the dispatcher refuses
the call without it.

Identity comes from the verified token only (the ``admin`` argument,
built from ``auth_user`` by ``protocol._admin_from_user``) — never from
tool arguments. ``team_approve`` takes the caller's PropelAuth id;
``team_message`` gets an adapter object carrying the token's id, email,
and name, because ``send_team_message`` reads those attributes off the
admin user for its Slack audit line.

Service functions return either a bare dict (HTTP 200 in REST) or a
``(payload, status)`` tuple on refusals; ``_as_tuple`` normalizes both
to the tuple the dispatcher expects, passing refusals through unchanged
so MCP clients see the same codes as REST (``already_approved`` 409,
``not_in_review`` 409, ...).

Gateway keys: ``team_gateway_key_statuses`` and the rotate/provision
results are metadata only — no plaintext or ciphertext ever passes
through these tools. Rotate/provision return the service payload
as-is; nothing is added to any audit payload.
"""
import types
from typing import Any, Dict, Tuple

from api.mcp.registry import VOLUNTEER_ADMIN, register_tool
from api.teams.gateway_keys import (
    MAX_STATUS_BATCH,
    list_gateway_key_statuses,
    provision_team_gateway_key,
    rotate_team_gateway_key,
)
from api.teams.teams_service import (
    add_team_member,
    approve_team,
    edit_team,
    get_queued_teams,
    remove_team,
    remove_team_member,
    send_team_message,
)

_PERMISSIONS = (VOLUNTEER_ADMIN,)

# The dispatcher rejects unknown arguments when a schema is closed
# (``additionalProperties: False``), and ``confirm`` is an argument — so
# every confirm-gated tool declares it in its schema.
_CONFIRM_PROPERTY = {"confirm": {"type": "boolean"}}


def _as_tuple(result):
    # type: (Any) -> Tuple[Dict[str, Any], int]
    """Normalize a service return to a ``(payload, status)`` tuple."""
    if isinstance(result, tuple):
        return result
    return result, 200


def _edit_body(arguments):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Build the JSON ``edit_team`` expects from the tool arguments.

    ``edit_team`` reads camelCase keys for two fields (``slackChannel``,
    ``nonprofitRankings``); everything else keeps its snake_case name.
    Only arguments the caller actually passed are forwarded, so an
    omitted field is left untouched.
    """
    body = {"id": arguments["team_id"]}  # type: Dict[str, Any]
    json_keys = {
        "slack_channel": "slackChannel",
        "nonprofit_rankings": "nonprofitRankings",
    }
    for field in (
        "name",
        "slack_channel",
        "github_username",
        "comments",
        "nonprofit_rankings",
        "selected_nonprofit_id",
        "status",
        "team_number",
        "active",
        "hackathon_event_id",
        "location",
        "admin_notes",
        "devpost_link",
        "demo_video_url",
        "project_tagline",
        "project_story",
        "project_built_with",
        "project_links",
        "project_thumbnail_url",
        "project_images",
        "project_submission_status",
        "problem_statement_id",
        "created",
    ):
        if field in arguments:
            body[json_keys.get(field, field)] = arguments[field]
    return body


def _admin_user(admin):
    # type: (Dict[str, Any]) -> Any
    """Adapter for ``send_team_message``: it reads ``first_name`` /
    ``last_name`` / ``email`` / ``user_id`` attributes off the admin user
    for its display name, so hand it an object with those attributes
    built from the token identity."""
    name = admin.get("name") or ""
    first, _, last = name.partition(" ")
    return types.SimpleNamespace(
        user_id=admin.get("propel_id"),
        email=admin.get("email"),
        first_name=first,
        last_name=last,
    )


@register_tool(
    "team_list_queued",
    "List teams with status IN_REVIEW awaiting nonprofit approval.",
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    _PERMISSIONS,
)
def team_list_queued(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return _as_tuple(get_queued_teams())


@register_tool(
    "team_approve",
    "Approve a queued team and pair it with a nonprofit project: sets "
    "NONPROFIT_SELECTED, creates the GitHub repo, posts to Slack, and "
    "mints the team's AI gateway keys. Refuses with already_approved / "
    "not_in_review (409) when the team is not waiting for review.",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "nonprofit_id": {"type": "string"},
                "problem_statement_id": {"type": "string"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id", "nonprofit_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_approve(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    body = {
        "teamId": arguments["team_id"],
        "nonprofitId": arguments["nonprofit_id"],
    }  # type: Dict[str, Any]
    if "problem_statement_id" in arguments:
        body["problemStatementId"] = arguments["problem_statement_id"]
    return _as_tuple(approve_team(admin.get("propel_id"), body))


@register_tool(
    "team_edit",
    "Edit a team (name, Slack channel, GitHub username, status, project "
    "write-up fields, or move it to a different problem statement). Only "
    "the fields passed are changed.",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "name": {"type": "string"},
                "slack_channel": {"type": "string"},
                "github_username": {"type": "string"},
                "comments": {"type": "string"},
                "nonprofit_rankings": {"type": "array"},
                "selected_nonprofit_id": {"type": "string"},
                "status": {"type": "string"},
                "team_number": {"type": "integer"},
                "active": {"type": "boolean"},
                "hackathon_event_id": {"type": "string"},
                "location": {"type": "string"},
                "admin_notes": {"type": "string"},
                "devpost_link": {"type": "string"},
                "demo_video_url": {"type": "string"},
                "project_tagline": {"type": "string"},
                "project_story": {"type": "string"},
                "project_built_with": {"type": "string"},
                "project_links": {"type": "array"},
                "project_thumbnail_url": {"type": "string"},
                "project_images": {"type": "array"},
                "project_submission_status": {
                    "type": "string",
                    "enum": ["draft", "submitted", "late"],
                },
                "problem_statement_id": {"type": "string"},
                "created": {"type": "string"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_edit(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return _as_tuple(edit_team(_edit_body(arguments)))


@register_tool(
    "team_add_member",
    "Add a user to a team's roster (admin roster edit; notifies the user "
    "on Slack).",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "user_id": {"type": "string"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id", "user_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_add_member(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return _as_tuple(add_team_member(arguments["team_id"], arguments["user_id"]))


@register_tool(
    "team_remove_member",
    "Remove a user from a team's roster (admin roster edit; notifies the "
    "user on Slack).",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "user_id": {"type": "string"},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id", "user_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_remove_member(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return _as_tuple(remove_team_member(arguments["team_id"], arguments["user_id"]))


@register_tool(
    "team_remove",
    "Delete a team: removes it from its members and its hackathon event, "
    "then deletes the team document.",
    {
        "type": "object",
        "properties": dict(
            {"team_id": {"type": "string"}},
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_remove(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return _as_tuple(remove_team(arguments["team_id"]))


@register_tool(
    "team_message",
    "Send an admin message to a team over Slack, email, or both "
    "(Slack only by default).",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "message": {"type": "string"},
                "subject": {"type": "string"},
                "channels": {
                    "type": "object",
                    "properties": {
                        "slack": {"type": "boolean"},
                        "email": {"type": "boolean"},
                    },
                },
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id", "message"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_message(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    body = {"message": arguments["message"]}  # type: Dict[str, Any]
    if "subject" in arguments:
        body["subject"] = arguments["subject"]
    if "channels" in arguments:
        body["channels"] = arguments["channels"]
    return _as_tuple(
        send_team_message(_admin_user(admin), arguments["team_id"], body)
    )


@register_tool(
    "team_gateway_key_statuses",
    "AI gateway key status (metadata only — never plaintext or "
    "ciphertext) for up to 200 teams at once, for the admin Teams table.",
    {
        "type": "object",
        "properties": {
            "team_ids": {"type": "array"},
        },
        "required": ["team_ids"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
)
def team_gateway_key_statuses(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    team_ids = arguments["team_ids"]
    if not team_ids:
        return {"error": "team_ids_required"}, 400
    if len(team_ids) > MAX_STATUS_BATCH:
        return {"error": "too_many_team_ids", "max": MAX_STATUS_BATCH}, 400
    return {"keys": list_gateway_key_statuses(team_ids)}, 200


@register_tool(
    "team_rotate_gateway_key",
    "Rotate one of a team's AI gateway keys (key1 or key2): deletes the "
    "old key in LiteLLM and mints a fresh one under the same alias. Only "
    "the named key is touched. Returns key metadata only, never the key.",
    {
        "type": "object",
        "properties": dict(
            {
                "team_id": {"type": "string"},
                "key": {"type": "string", "enum": ["key1", "key2"]},
            },
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id", "key"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_rotate_gateway_key(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    try:
        return rotate_team_gateway_key(arguments["team_id"], arguments["key"]), 200
    except RuntimeError as e:
        return {"error": str(e)}, 404
    except Exception as e:
        return {"error": "rotation failed: %s" % e}, 502


@register_tool(
    "team_provision_gateway_key",
    "Provision (or re-provision) a team's AI gateway keys. Idempotent: "
    "returns both keys' metadata when they already exist. Covers the "
    "case where approval's best-effort mint failed. Returns key metadata "
    "only, never the keys.",
    {
        "type": "object",
        "properties": dict(
            {"team_id": {"type": "string"}},
            **_CONFIRM_PROPERTY
        ),
        "required": ["team_id"],
        "additionalProperties": False,
    },
    _PERMISSIONS,
    requires_confirm=True,
)
def team_provision_gateway_key(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    try:
        return provision_team_gateway_key(arguments["team_id"]), 200
    except Exception as e:
        return {"error": "provisioning failed: %s" % e}, 502
