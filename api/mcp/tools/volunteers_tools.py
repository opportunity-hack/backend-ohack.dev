"""Volunteer admin tools for the admin MCP server.

Thin wrappers over ``services.volunteers_service`` — the same service
layer behind ``api/volunteers/volunteers_views.py``. Every handler
returns the service ``(payload, status)`` tuple (or the REST view's
error mapping where the service raises instead of returning a tuple);
no business logic lives here. Caller identity comes from the verified
token only (the ``admin`` argument); tool arguments never carry a user
id.

Tools (all ``volunteer.admin``-only):

- ``volunteers_list_by_event`` — roster for an event, by type.
- ``volunteers_select`` — the ONLY writer of ``isSelected`` (event
  roster); ``selected`` must be a JSON boolean. The boolean is enforced
  by the tool's JSON Schema (the dispatcher rejects anything else with
  ``-32602``), the same rule the REST view enforces with its
  ``isinstance(selected, bool)`` check — the service itself takes the
  value as given.
- ``volunteers_refund_deposit`` — Stripe refund of one hacker's deposit.
  Moves money and is irreversible (Stripe is called first, Firestore
  second), so the ``confirm: true`` gate is load-bearing.
- ``volunteers_bulk_refund_deposits`` — refunds every hacker for an
  event whose deposit is paid + disposition=refund. Also moves money,
  also ``confirm: true``. Donate-disposition hackers are excluded by
  the service (override is a per-row decision); per-row failures are
  collected, not fatal.
- ``volunteers_message`` — admin message to a volunteer (Slack + email).
  Sends outbound mail, so ``confirm: true``.
- ``volunteers_resend_status`` — Resend delivery status: per-ID
  statuses when ``email_ids`` is given, otherwise the cached email
  index (optionally filtered by recipient ``emails``, ``force`` kicks
  off a background cache refresh). Read-only; no ``confirm``.

Omitted on purpose (pinned from ``services.volunteers_service``):

- ``find_volunteer_by_caller_identity`` — the shared 3-way identity
  resolver, not a tool. No volunteer tool needs caller identity: every
  tool addresses a volunteer by id or an event by id, and the admin
  identity comes from the token.
- ``send_email_to_address`` — a generic send-to-any-address endpoint,
  not volunteer-scoped; admin-to-volunteer messaging is covered by
  ``volunteers_message``.
- The self-service application flows (submit/update/get-my-application,
  confirmation emails, mentor check-in/out) and the Stripe webhook
  handler — not admin tools.
"""
import types
from typing import Any, Dict, Tuple

from common.log import get_logger
from api.mcp.registry import VOLUNTEER_ADMIN, register_tool
from services.volunteers_service import (
    bulk_refund_eligible_hacker_deposits,
    get_resend_email_statuses,
    get_volunteers_by_event,
    list_all_resend_emails,
    refund_hacker_deposit,
    send_volunteer_message,
    update_volunteer_selection,
)

logger = get_logger(__name__)

_VOLUNTEER_TYPES = ("mentor", "judge", "hacker", "volunteer", "sponsor")
_MAX_LIST_LIMIT = 200


def _audit(action, message):
    # type: (str, str) -> None
    """Slack audit line for a money movement. The refund service does not
    audit itself — the REST views do — so the tools must post the same
    line or an MCP refund leaves no record of who refunded what. (The
    dispatcher's own audit carries the tool name only, never arguments.)
    Best-effort: a Slack failure never fails a refund that already
    happened."""
    try:
        from common.utils.slack import send_slack_audit

        send_slack_audit(action=action, message=message)
    except Exception as e:
        logger.warning("MCP audit %s failed: %s", action, type(e).__name__)
_DEFAULT_MESSAGE_SUBJECT = "Message from Opportunity Hack Team"


def _admin_user(admin):
    # type: (Dict[str, Any]) -> Any
    """The admin identity as an object, matching the ``auth_user`` shape
    the service layer reads (``admin_user.user_id`` for the sent-by
    audit line in ``send_volunteer_message``)."""
    return types.SimpleNamespace(
        user_id=admin.get("propel_id"),
        email=admin.get("email"),
        name=admin.get("name"),
    )


def _success(data):
    # type: (Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    payload = {"success": True}
    payload.update(data)
    return payload, 200


def _failure(message, status):
    # type: (str, int) -> Tuple[Dict[str, Any], int]
    return {"success": False, "error": message}, status


@register_tool(
    "volunteers_list_by_event",
    "List volunteer applications for an event, by type, with pagination "
    "and an optional isSelected filter. page starts at 1; limit is 1-200 "
    "(default 20).",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "type": {"type": "string", "enum": list(_VOLUNTEER_TYPES)},
            "page": {"type": "integer"},
            "limit": {"type": "integer"},
            "selected": {"type": "boolean"},
        },
        "required": ["event_id", "type"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def volunteers_list_by_event(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    # The service turns these straight into a Firestore offset/limit: a
    # page below 1 is a negative offset (a crash), and an unbounded limit
    # is the whole roster in one result.
    page = max(1, arguments.get("page", 1))
    limit = max(1, min(arguments.get("limit", 20), _MAX_LIST_LIMIT))
    volunteers = get_volunteers_by_event(
        arguments["event_id"],
        arguments["type"],
        page,
        limit,
        arguments.get("selected"),
    )
    return _success(
        {
            "volunteers": volunteers,
            "page": page,
            "limit": limit,
            # Same caveat as the REST admin list: this counts the page,
            # not the event — the service has no total-count query.
            "total": len(volunteers),
        }
    )


@register_tool(
    "volunteers_select",
    "Set a volunteer's isSelected flag (event roster). This is the ONLY "
    "writer of isSelected; selected must be a JSON boolean. Requires "
    "confirm: true.",
    {
        "type": "object",
        "properties": {
            "volunteer_id": {"type": "string"},
            "selected": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        },
        "required": ["volunteer_id", "selected"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def volunteers_select(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    updated = update_volunteer_selection(
        arguments["volunteer_id"],
        arguments["selected"],
        admin.get("propel_id"),
    )
    if updated is None:
        return _failure("Volunteer not found", 404)
    return _success({"volunteer": updated})


@register_tool(
    "volunteers_refund_deposit",
    "Issue a Stripe refund for one hacker's deposit and mark it refunded. "
    "This moves real money and is irreversible: Stripe is charged back "
    "first and Firestore second, so a failed Firestore write after a "
    "successful refund still leaves the money refunded. Requires "
    "confirm: true. Pass override: true to refund a hacker who chose to "
    "donate their deposit.",
    {
        "type": "object",
        "properties": {
            "volunteer_id": {"type": "string"},
            "override": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        },
        "required": ["volunteer_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def volunteers_refund_deposit(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    override = bool(arguments.get("override", False))
    try:
        updated = refund_hacker_deposit(
            volunteer_id=arguments["volunteer_id"],
            admin_user_id=admin.get("propel_id"),
            override=override,
        )
    except ValueError as e:
        # Caller-facing validation: not a hacker, no deposit, already
        # refunded, donate-disposition without override. Same 400 as REST.
        return _failure(str(e), 400)
    except RuntimeError as e:
        # Stripe rejection or missing STRIPE_SECRET_KEY. Same 502 as REST.
        return _failure(str(e), 502)
    _audit(
        "hacker_deposit_refund",
        "%s refunded hacker deposit for volunteer %s via MCP "
        "(refund_id=%s, amount_cents=%s, override=%s)"
        % (
            admin.get("propel_id"),
            arguments["volunteer_id"],
            (updated or {}).get("deposit_refund_id"),
            (updated or {}).get("deposit_refund_amount_cents"),
            override,
        ),
    )
    return _success({"volunteer": updated})


@register_tool(
    "volunteers_bulk_refund_deposits",
    "Refund every hacker for an event whose deposit is paid and whose "
    "disposition is refund. This moves real money: one Stripe refund per "
    "eligible hacker. Requires confirm: true. Donate-disposition hackers "
    "are excluded by design (override is a per-row decision) and "
    "per-row failures are collected, not fatal.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def volunteers_bulk_refund_deposits(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    try:
        result = bulk_refund_eligible_hacker_deposits(
            event_id=arguments["event_id"],
            admin_user_id=admin.get("propel_id"),
        )
    except Exception as e:
        logger.exception("MCP bulk refund failed for %s", arguments["event_id"])
        return _failure("Bulk refund failed: %s" % e, 500)
    result = result or {}
    _audit(
        "hacker_deposit_bulk_refund",
        "%s ran bulk refund for event %s via MCP: %d refunded ($%.2f), %d failed"
        % (
            admin.get("propel_id"),
            arguments["event_id"],
            len(result.get("refunded") or []),
            (result.get("total_amount_cents") or 0) / 100.0,
            len(result.get("failed") or []),
        ),
    )
    return _success(result)


@register_tool(
    "volunteers_message",
    "Send an admin message to a volunteer via Slack and email. Requires "
    "confirm: true: this sends outbound messages the volunteer will see.",
    {
        "type": "object",
        "properties": {
            "volunteer_id": {"type": "string"},
            "message": {"type": "string"},
            "subject": {"type": "string"},
            "recipient_type": {"type": "string"},
            "recipient_id": {"type": "string"},
            "email": {"type": "string"},
            "name": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["volunteer_id", "message"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def volunteers_message(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    if not arguments["message"].strip():
        # Same 400 as the REST view; never send an empty Slack DM / email.
        return _failure("Message is required", 400)
    result = send_volunteer_message(
        volunteer_id=arguments["volunteer_id"],
        message=arguments["message"],
        subject=arguments.get("subject", _DEFAULT_MESSAGE_SUBJECT),
        admin_user_id=admin.get("propel_id"),
        admin_user=_admin_user(admin),
        recipient_type=arguments.get("recipient_type", "volunteer"),
        recipient_id=arguments.get("recipient_id", arguments["volunteer_id"]),
        fallback_email=arguments.get("email"),
        fallback_name=arguments.get("name"),
    )
    if result.get("success"):
        return result, 200
    # Same error mapping as the REST view.
    if result.get("error") == "Volunteer not found":
        return result, 404
    if result.get("error") == "Volunteer email not found":
        return result, 400
    return result, 500


@register_tool(
    "volunteers_resend_status",
    "Resend email delivery status (read-only). With email_ids, fetch the "
    "delivery status of those Resend email IDs; without them, return the "
    "cached index of sent emails, optionally filtered to recipient "
    "emails, with force: true kicking off a background refresh of the "
    "index.",
    {
        "type": "object",
        "properties": {
            "email_ids": {"type": "array", "items": {"type": "string"}},
            "emails": {"type": "array", "items": {"type": "string"}},
            "force": {"type": "boolean"},
        },
        "required": [],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def volunteers_resend_status(arguments, admin):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    email_ids = arguments.get("email_ids")
    if email_ids is not None:
        if not isinstance(email_ids, list) or not email_ids:
            return _failure("email_ids array is required", 400)
        result = get_resend_email_statuses(email_ids)
    else:
        filter_emails = arguments.get("emails") or None
        result = list_all_resend_emails(
            filter_emails=filter_emails,
            force=bool(arguments.get("force", False)),
        )
    if result.get("success"):
        return result, 200
    return result, 500
