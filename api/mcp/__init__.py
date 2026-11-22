"""Admin-only MCP server (Model Context Protocol, Streamable HTTP).

Exposes the backend's existing admin service layer as MCP tools at
``POST /api/mcp``. Admin-only by construction: the endpoint requires a
PropelAuth Bearer token plus ``X-Org-Id`` (``volunteer.admin`` or
``checkin.admin`` at the gate), and the dispatcher re-checks each tool's
own permission list before calling its handler. No public routes, no
anonymous access, no new credentials.

Tools live in ``api/mcp/tools/``; each module registers its tools at
import time through ``api.mcp.registry``. ``tools/__init__.py`` imports
every module in that package, so adding a tool module needs no registry
edit. v1 is tools only: no resources, prompts, sampling, or elicitation.
"""
