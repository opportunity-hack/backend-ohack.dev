"""Reimbursement admin tools for the MCP server.

Thin wrappers over ``api.reimbursements.reimbursements_service`` — no
business logic lives here. All three tools are ``volunteer.admin``-only.
``reimbursement_review`` moves claim state, so it requires ``confirm:
true`` (the dispatcher refuses the call without it).

Identity comes from the verified token only: the dispatcher passes the
reviewer dict (``{"propel_id", "email", "name"}``) built from
``auth_user``; tool arguments never carry a reviewer. The review payload
forwards only ``status`` / ``reviewNote`` / ``paymentReference`` — the
service ignores everything else, and no payout data (handles, bank
details) is ever put in an audit payload (the MCP audit in
``mcp_views`` sends tool name + caller + outcome only, never arguments).
No bank details are stored by the service in the first place.
"""
from typing import Any, Dict, Tuple

from api.mcp.registry import VOLUNTEER_ADMIN, register_tool
from api.reimbursements import reimbursements_service
from api.reimbursements.reimbursements_service import STATUSES

_STATUSES = list(STATUSES)


@register_tool(
    "reimbursement_list",
    "List reimbursement claims for admin review, newest first. "
    "Optional status / event filters; limit defaults to 50, max 200.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _STATUSES},
            "event_id": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def reimbursement_list(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return reimbursements_service.admin_list_reimbursements(
        status=arguments.get("status"),
        event_id=arguments.get("event_id"),
        limit=arguments.get("limit", 50),
        offset=arguments.get("offset", 0),
    )


@register_tool(
    "reimbursement_get",
    "Full admin detail for one reimbursement claim: receipt URL, "
    "status history, reviewer info.",
    {
        "type": "object",
        "properties": {
            "reimbursement_id": {"type": "string"},
        },
        "required": ["reimbursement_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def reimbursement_get(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    return reimbursements_service.admin_get_reimbursement(
        arguments["reimbursement_id"]
    )


@register_tool(
    "reimbursement_review",
    "Review a reimbursement claim: move it to a new status with an "
    "optional review note. Marking a claim paid requires "
    "paymentReference (the transaction id, check number, or ACH "
    "trace). A reviewer cannot change their own claim (the service "
    "answers 403 self_approval). Requires confirm: true.",
    {
        "type": "object",
        "properties": {
            "reimbursement_id": {"type": "string"},
            "status": {"type": "string", "enum": _STATUSES},
            "reviewNote": {"type": "string"},
            "paymentReference": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["reimbursement_id", "status"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def reimbursement_review(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    # Only the fields the service reads; anything else in the arguments
    # (there is nothing else — the schema is closed) is never forwarded.
    data = {"status": arguments["status"]}  # type: Dict[str, Any]
    if "reviewNote" in arguments:
        data["reviewNote"] = arguments["reviewNote"]
    if "paymentReference" in arguments:
        data["paymentReference"] = arguments["paymentReference"]
    return reimbursements_service.admin_review_reimbursement(
        arguments["reimbursement_id"], data, admin
    )
