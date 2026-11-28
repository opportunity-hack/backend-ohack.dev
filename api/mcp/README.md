# Admin MCP server (`api/mcp/`)

An MCP server (Streamable HTTP, stateless) that exposes the backend's
existing admin service layer as tools. Admin-only: there is no public
MCP, no anonymous access, and no new credential type.

## Endpoint

- `POST /api/mcp` — the only MCP route. `GET` returns 405 (no
  server-initiated notifications in v1).
- Stateless per the MCP 2026-07-28 revision: no sessions, no
  `Mcp-Session-Id`, no required `initialize` handshake. `initialize` is
  answered for older clients but never required (it echoes the client's
  `protocolVersion` when that is one of
  `protocol.SUPPORTED_PROTOCOL_VERSIONS`, else answers the latest);
  plain JSON responses, not SSE streams.

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
   (`checkin_roster`, `checkin_checked_in`, `checkin_check_in`);
   `checkin_reset` stays `volunteer.admin`-only.

### Personal access tokens

A PropelAuth access token lasts 30 minutes, which does not suit an MCP
client config. An admin can mint a personal access token instead
(`/admin/mcp` on the site, or the routes below) and send it as the
Bearer token. No `X-Org-Id` is needed: the org is stored on the token.

- `GET /api/mcp/tokens` — the caller's tokens (never the raw value)
- `POST /api/mcp/tokens` `{name, expiresInDays?}` — returns `token` once
- `DELETE /api/mcp/tokens/<id>` — revoke (deletes the doc)

Rules (`api/mcp/tokens_service.py`, collection `mcp_tokens`):

- Only the SHA-256 of a token is stored (doc id). Tokens start `ohmcp_`.
- A token carries no permissions. Every use re-reads the owner's current
  permissions from PropelAuth (cached 60s), so removing a role or
  disabling the account cuts the token off. PropelAuth unreachable →
  503, never a pass.
- Expiry 1–90 days (default 90); at most 5 active tokens per person.
- The three token routes take a PropelAuth session only, so a personal
  token cannot mint, list or revoke tokens.
- The Slack audit line for a `tools/call` carries `token_id`; minting
  and revoking are audited as `mcp_token_created` / `mcp_token_revoked`.

Identity for writes comes from the verified token only. Tool arguments
never carry a user id, reviewer id, or `approvedBy`. Token lifetime
and revocation follow PropelAuth exactly as REST does; there are no
long-lived MCP tokens in v1. For non-interactive admin clients, the
operator obtains a token through the existing login flow.

Every `tools/call` writes a structured log line and a fire-and-forget
`send_slack_audit(action="mcp_tool_call", ...)` with the tool name,
caller PropelAuth id, and outcome (arguments are not sent). A call the
dispatcher refuses (`-32001`, `-32602`, ...) or that crashes (`-32603`)
is audited with `is_error: True`.

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
refuses the call with `-32602` without it. The registry adds `confirm`
to the listed schema of every `requires_confirm` tool (as a required
boolean), so a tool module cannot forget to advertise it.

An argument sent as JSON `null` is treated as absent (model clients
send `null` for optional arguments they do not use). Payloads are
serialized with `default=str`, so datetimes in a service payload are
fine.

## Adding a tool

1. Add a module in `api/mcp/tools/` (one per domain, e.g.
   `meet.py`). Register each tool with `@register_tool` — name,
   description, JSON Schema for arguments, permission tuple, and
   `requires_confirm` where it moves state.
2. Handlers are thin: validate nothing the dispatcher hasn't already,
   resolve caller identity from the `admin` argument, call the existing
   service function, return its `(payload, status)` tuple. No duplicated
   business logic, no HTTP calls back into the app's own REST routes.
3. `tools/__init__.py` imports every module in the package, so a new
   tool module needs no registry edit.
4. Add tests in `api/mcp/tests/test_mcp_<domain>.py` (see below),
   including the module's own `tools/list` assertion — check that your
   names are IN the list, never that the list equals them, and leave
   `test_mcp_auth.py` alone. Document the tools under your heading in
   the catalog below.

## Tool catalog

One subsection per domain module. Each domain PR replaces only the
placeholder line under its own heading, so the stacked PRs merge without
touching each other's lines.

### Meetings (`feat/mcp-meet`)

`api/mcp/tools/meet.py`, all `volunteer.admin`-only, thin wrappers over
`api/meet/meet_service.py`:

- `meet_list_polls` (optional `status` filter, applied here — the
  service has none), `meet_get_poll` — read-only.
- `meet_create_poll`, `meet_update_poll`, `meet_delete_poll`,
  `meet_delete_vote`, `meet_finalize_poll` — writers, `confirm: true`.
  Closing a poll is `meet_update_poll` with `status: "closed"` (there
  is no cancel tool). Finalize creates the Google Calendar event + Meet
  link; a calendar failure leaves the poll open and re-finalizing is
  409. Deleting a poll does not cancel a sent calendar event.

### Reimbursements (`feat/mcp-reimbursements`)

_Not merged yet — `reimbursement_*` (admin review)._

### Teams (`feat/mcp-teams`)

_Not merged yet — `team_*` (approve, roster, messaging, gateway keys)._

### Check-in (`feat/mcp-checkin`)

_Not merged yet — `checkin_*` (desk tools, per-tool permissions)._

### Surveys (`feat/mcp-surveys`)

_Not merged yet — `survey_*` (read-only admin analytics)._

### Volunteers (`feat/mcp-volunteers`)

_Not merged yet — `volunteers_*` (roster, select, refunds)._

### Project completion (`feat/mcp-completion`)

`api/mcp/tools/completion.py`, all `volunteer.admin`-only:

- `completion_team_status` (`team_id`) — submission status
  (`draft` / `submitted` / `late`), submitted-at, which required
  write-up fields (`project_tagline`, `project_story`) are missing,
  Slack channel, linked GitHub repo. Reads the full team doc
  (`services.teams_service.get_team_admin`).
- `completion_event_status` (`hackathon_id`) — rollup: total teams,
  submitted vs draft, per-team rows. `hackathon_id` is the hackathon's
  database document id (what `GET /api/team/<hackathon_id>` takes),
  NOT the `event_id` slug; an unknown id reads as zero teams.
- `completion_mark_submitted` (`team_id`) — `confirm: true`; the only
  status writer: `submit_project(admin=True)`, recorded `late` past the
  close, idempotent (`already_submitted`), 400 `incomplete` when the
  write-up is missing fields.
- `completion_github_activity` (`team_id`) — commits, contributors,
  open PRs for the repo in the team's `github_links`; service errors
  pass through (`repo_not_found` 404, `github_rate_limited` 503,
  `github_unavailable` 502), plus 404 `no_github_repo`.

No Slack stand-up tool: the backend has no channel-history reader, so
that would be new service code, not a wrapper.

## Client setup

MCP Inspector (Streamable HTTP transport):

- URL: `https://<backend-host>/api/mcp`
- Headers: `Authorization: Bearer <token>`, `X-Org-Id: <org>`

Claude Code, with a personal access token (keep it out of committed
config — `claude mcp add` defaults to local scope):

```
claude mcp add --transport http ohack-admin https://<backend-host>/api/mcp \
  --header "Authorization: Bearer ohmcp_..."
```

Claude Code with a PropelAuth session token (`.mcp.json`):

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
sys.modules.pop("api.mcp.tools.meet", None)
sys.modules.pop("api.mcp.mcp_views", None)
views = importlib.import_module("api.mcp.mcp_views")
```

Two gotchas the snippet does not show:

- `from api.meet import meet_service` resolves through the parent
  package's attribute when the real service module was already imported
  (by an earlier test file), so the `sys.modules` stub is bypassed and
  the real service runs. Point the parent attribute at the stub too:
  `monkeypatch.setattr(sys.modules["api.meet"], "meet_service", stub, raising=False)`.
- Re-importing a tool module re-runs its `@register_tool` decorators,
  which raise "already registered" unless the module's own names are
  removed from `registry.TOOL_REGISTRY` first. Save and restore the
  registry around the test (`saved = dict(registry.TOOL_REGISTRY)` ...
  `TOOL_REGISTRY.clear(); TOOL_REGISTRY.update(saved)`) so other test
  files keep their tools.
