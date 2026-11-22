"""POST /api/mcp — the admin-only MCP endpoint (Streamable HTTP, stateless).

Gate, in order, on every request:

1. ``require_any_permission`` (``volunteer.admin`` OR ``checkin.admin``)
   — no token -> 401, no ``X-Org-Id`` -> 403. The gate is any-of on
   purpose: the dispatcher enforces each tool's own permission list, and
   a ``checkin.admin``-only caller must reach the three check-in tools.
   (A ``volunteer.admin``-only gate here would 403 them before dispatch.)
2. The dispatcher re-checks the tool's permissions for the caller and
   filters ``tools/list`` to what the caller may call.

No GET endpoint in v1 (no server-initiated notifications): GET is 405.
Identity for writes comes from the verified token only; tool arguments
never carry a user id. Every ``tools/call`` writes a structured log line
and a fire-and-forget Slack audit entry.
"""
import os

from flask import Blueprint, jsonify, request

from common.auth import (
    CHECKIN_PERMISSIONS,
    auth_user,
    getOrgId,
    require_any_permission,
    user_has_any_permission,
)
from common.log import get_logger
from api.mcp import protocol
from api.mcp.tools import register_all

logger = get_logger(__name__)

bp = Blueprint("mcp", __name__, url_prefix="/api")

register_all()


def _audit_tool_call(tool_name, caller, is_error):
    # type: (object, dict, object) -> None
    """Best-effort Slack audit line. Never raises; silent under
    ENVIRONMENT=test. Arguments are not sent: they contain no secrets by
    design, but the tool name + caller + outcome is the audit trail."""
    if os.environ.get("ENVIRONMENT") == "test":
        return
    try:
        from common.utils.slack import send_slack_audit

        send_slack_audit(
            action="mcp_tool_call",
            message="MCP tool %s called by %s"
            % (tool_name, caller.get("propel_id") or caller.get("email") or "unknown"),
            payload={
                "tool": tool_name,
                "caller": caller.get("propel_id"),
                "is_error": bool(is_error),
            },
        )
    except Exception as e:
        logger.warning("MCP audit failed (%s): %s", tool_name, type(e).__name__)


@bp.route("/mcp", methods=["POST"])
@require_any_permission(CHECKIN_PERMISSIONS, req_to_org_id=getOrgId)
def mcp_endpoint():
    body = request.get_json(silent=True)
    # Unparsable or empty JSON -> the dispatcher answers -32700.
    response, status = protocol.dispatch(
        body, auth_user, user_has_any_permission
    )
    if (
        isinstance(body, dict)
        and body.get("method") == "tools/call"
        and response is not None
    ):
        params = body.get("params")
        if not isinstance(params, dict):
            params = {}
        caller = protocol._admin_from_user(auth_user)
        # A JSON-RPC error (forbidden, bad params, handler crash) is a
        # failed call too: the audit must not record it as a success.
        if "error" in response:
            is_error = True
        else:
            result = response.get("result")
            is_error = bool(result.get("isError")) if isinstance(result, dict) else False
        # The name is client text and may not match any tool: keep the
        # log / Slack line short and single-line.
        tool_name = " ".join(str(params.get("name")).split())[:80]
        logger.info(
            "MCP tools/call %s by %s is_error=%s",
            tool_name,
            caller.get("propel_id"),
            is_error,
        )
        _audit_tool_call(tool_name, caller, is_error)
    if response is None:
        return "", status
    return jsonify(response), status


@bp.route("/mcp", methods=["GET"])
def mcp_get_not_allowed():
    return (
        jsonify(
            {
                "error": "method_not_allowed",
                "message": "This endpoint only accepts POST",
            }
        ),
        405,
    )
