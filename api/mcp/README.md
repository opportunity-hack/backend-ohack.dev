# Admin MCP server (`api/mcp/`)

An MCP server (Streamable HTTP, stateless) that exposes the backend's
existing admin service layer as tools. Admin-only: there is no public
MCP, no anonymous access, and no new credential type.

## Endpoint

- `POST /api/mcp` — the only MCP route. `GET` returns 405 (no
  server-initiated notifications in v1).
- Stateless per the MCP 2026-07-28 revision: no sessions, no
  `Mcp-Session-Id`, no required `initialize` handshake. `initialize` is
  answered for older clients but never required; plain JSON responses,
  not SSE streams.

## Auth

The client sends the same credential the frontend sends:

- `Authorization: Bearer <PropelAuth access token>`
- `X-Org-Id: <org>`

Gate, in order, on every request:

1. The endpoint requires `volunteer.admin` **or** `checkin.admin`
   (`require_any_permission`). No token → 401, no `X-Org-Id` → 403.
   The gate is any-of on purpose: the dispatcher enforces each tool's
   own permission list, and a `checkin.admin`-only caller must reach
   the three check-in tools. A `volunteer.admin`-only gate here would
   403 them before dispatch.
2. The dispatcher re-checks the tool's permissions for the caller
   (defense in depth) and filters `tools/list` to what the caller may
   call. All tools are `volunteer.admin`-only except the three
   check-in tools, which also accept `checkin.admin`
   (`checkin_roster`, `checkin_list_checked_in`, `checkin_check_in`);
   `checkin_reset` stays `volunteer.admin`-only.

Identity for writes comes from the verified token only. Tool arguments
never carry a user id, reviewer id, or `approvedBy`. Token lifetime
and revocation follow PropelAuth exactly as REST does; there are no
long-lived MCP tokens in v1. For non-interactive admin clients, the
operator obtains a token through the existing login flow.

Every `tools/call` writes a structured log line and a fire-and-forget
`send_slack_audit(action="mcp_tool_call", ...)` with the tool name,
caller PropelAuth id, and outcome (arguments are not sent).

## Protocol

Hand-rolled JSON-RPC 2.0 dispatcher (`protocol.py`) — the official MCP
Python SDK needs Python 3.10+ and local dev is 3.9. Methods:
`initialize`, `ping`, `tools/list`, `tools/call`, `notifications/*`
(no-ops). JSON-RPC errors: `-32700` parse error, `-32600` invalid
request, `-32601` method/tool not found, `-32602` invalid params,
`-32603` internal error (details to the server log only), `-32001`
forbidden. Service-layer `{error, message}` payloads pass through
unchanged as MCP `isError` results, so clients see the same codes as
REST (`self_approval` 403, `already_approved` 409, ...).

Side-effecting tools take a required boolean `confirm: true` argument
(finalize a poll, approve a team, reset check-ins, refund a deposit,
...). A model cannot move money or state by accident; the dispatcher
refuses the call with `-32602` without it.

## Adding a tool

1. Add a module in `api/mcp/tools/` (one per domain, e.g.
   `meet_tools.py`). Register each tool with `@register_tool` — name,
   description, JSON Schema for arguments, permission tuple, and
   `requires_confirm` where it moves state.
2. Handlers are thin: validate nothing the dispatcher hasn't already,
   resolve caller identity from the `admin` argument, call the existing
   service function, return its `(payload, status)` tuple. No duplicated
   business logic, no HTTP calls back into the app's own REST routes.
3. `tools/__init__.py` imports every module in the package, so a new
   tool module needs no registry edit.
4. Add tests in `api/mcp/tests/test_<domain>_tools.py` (see below) and
   document the tool in the catalog below.

## Tool catalog

No tools are registered yet — this PR is the framework. Each domain PR
adds its module here:

- `feat/mcp-meet` — `meet_*` (scheduling polls; calendar finalize)
- `feat/mcp-reimbursements` — `reimbursements_*` (admin review)
- `feat/mcp-teams` — `team_*` (approve, roster, messaging, gateway keys):
  `team_list_queued` (IN_REVIEW teams), `team_approve` (confirm; creates
  the GitHub repo, posts to Slack, mints gateway keys),
  `team_edit` (confirm), `team_add_member` / `team_remove_member`
  (confirm), `team_remove` (confirm), `team_message` (confirm; Slack
  and/or email), `team_gateway_key_statuses` (metadata only, max 200
  teams), `team_rotate_gateway_key` (confirm; key1|key2),
  `team_provision_gateway_key` (confirm; idempotent re-provision).
  All `volunteer.admin`-only; identity comes from the token.
- `feat/mcp-checkin` — `checkin_*` (desk tools, per-tool permissions)
- `feat/mcp-surveys` — `surveys_*` (read-only admin analytics)
- `feat/mcp-volunteers` — `volunteers_*` (roster, select, refunds)
- `feat/mcp-completion` — `completion_*` (project completion status,
  GitHub activity; Slack stand-ups are NOT included — the backend
  tracks no stand-up activity, see that PR)

## Client setup

MCP Inspector (Streamable HTTP transport):

- URL: `https://<backend-host>/api/mcp`
- Headers: `Authorization: Bearer <token>`, `X-Org-Id: <org>`

Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "ohack-admin": {
      "type": "http",
      "url": "https://<backend-host>/api/mcp",
      "headers": {
        "Authorization": "Bearer <token>",
        "X-Org-Id": "<org>"
      }
    }
  }
}
```

Tokens are short-lived PropelAuth access tokens; refresh through the
normal login flow. Never commit a token, and never put one in a shared
config.

## Testing

```
ENVIRONMENT=test SLACK_WEBHOOK= SLACK_BOT_TOKEN= RESEND_API_KEY= \
  FIREBASE_CERT_CONFIG='<structurally valid throwaway service-account JSON>' \
  python -m pytest api/mcp/tests -q
```

- `test_mcp_protocol.py` — pure dispatcher tests: initialize/ping,
  tools/list filtering, argument validation, confirm gating,
  per-tool permissions, error mapping, notifications.
- `test_mcp_auth.py` — route tests with `test/common/auth_stubs.py`:
  anonymous → 401, org-less → 403, GET → 405, malformed JSON → -32700.
- Tool tests stub the service module in `sys.modules` (only via
  `monkeypatch.setitem`) **before** importing `api.mcp.mcp_views`, and
  reset `api.mcp.tools._registered` + pop the tool module from
  `sys.modules` so the tool module re-imports against the stub:

```python
monkeypatch.setitem(sys.modules, "api.meet.meet_service", stub)
import api.mcp.tools as tools_pkg
monkeypatch.setattr(tools_pkg, "_registered", False)
sys.modules.pop("api.mcp.tools.meet_tools", None)
sys.modules.pop("api.mcp.mcp_views", None)
views = importlib.import_module("api.mcp.mcp_views")
```
