"""Tool tests for completion_*.

The four service modules are stubbed in sys.modules (only via
monkeypatch.setitem) BEFORE importing api.mcp.mcp_views, and the tool
module is re-imported against the stubs — the pattern api/mcp/README.md
documents. The stubs prove dispatch, auth, validation, and error
mapping; the service layer's own tests cover the business rules.
"""
import functools
import importlib
import json
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

from test.common.auth_stubs import passthrough_auth_module

VIEWS = "api.mcp.mcp_views"
TOOLS_PKG = "api.mcp.tools"
TOOL_MODULE = "api.mcp.tools.completion"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}

TEAM_SUBMITTED = {
    "id": "team-1",
    "name": "Team Rocket",
    "status": "NONPROFIT_SELECTED",
    "project_submission_status": "submitted",
    "project_submitted_at": "2026-11-15T10:00:00+00:00",
    "project_tagline": "Doing good",
    "project_story": "A story",
    "slack_channel": "team-rocket",
    "github_links": [
        {"link": "https://github.com/opportunity-hack/TeamRocket-FoodBank", "name": "TeamRocket-FoodBank"}
    ],
}

TEAM_DRAFT = {
    "id": "team-2",
    "name": "Team Draft",
    "status": "NONPROFIT_SELECTED",
    "project_submission_status": "draft",
    "project_tagline": "",
    "project_story": "A story",
    "slack_channel": "team-draft",
    "github_links": [],
}

TEAM_DRAFT_COMPLETE = {
    "id": "team-3",
    "name": "Team Ready",
    "status": "NONPROFIT_SELECTED",
    "project_submission_status": "draft",
    "project_tagline": "Ready to go",
    "project_story": "A story",
    "slack_channel": "team-ready",
    "github_links": [],
}

TEAM_UNKNOWN_REPO = {
    "id": "team-4",
    "name": "Team Elsewhere",
    "status": "NONPROFIT_SELECTED",
    "project_submission_status": "submitted",
    "project_submitted_at": "2026-11-15T11:00:00+00:00",
    "project_tagline": "Elsewhere",
    "project_story": "A story",
    "slack_channel": "team-elsewhere",
    "github_links": [
        {"link": "https://github.com/opportunity-hack/OtherRepo", "name": "OtherRepo"}
    ],
}

TEAMS_BY_ID = {
    "team-1": TEAM_SUBMITTED,
    "team-2": TEAM_DRAFT,
    "team-3": TEAM_DRAFT_COMPLETE,
    "team-4": TEAM_UNKNOWN_REPO,
}


def _stub_module(name, **attrs):
    stub = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(stub, key, value)
    return stub


def _denying_permissions_auth_module():
    """Passes the route gate (token + org present) but the dispatcher
    sees no volunteer.admin permission, so every completion tool 403s."""
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=lambda *a, **k: (lambda fn: fn),
        require_user=lambda fn: fn,
        optional_user=lambda fn: fn,
    )
    stub.auth_user = LocalProxy(lambda: getattr(g, "propelauth_current_user", None))
    stub.getOrgId = lambda req: req.headers.get("X-Org-Id")
    stub.CHECKIN_PERMISSIONS = ("volunteer.admin", "checkin.admin")

    def _passthrough(*_args, **_kwargs):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                g.propelauth_current_user = types.SimpleNamespace(
                    user_id="fake-propel-uuid", email="fake@example.com"
                )
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    stub.require_any_permission = _passthrough
    stub.user_has_any_permission = lambda user, perms: False
    return stub


@pytest.fixture
def client(monkeypatch):
    state = {"submit_calls": []}

    def get_team_admin(team_id):
        team = TEAMS_BY_ID.get(team_id)
        return dict(team) if team is not None else None

    def get_teams_by_hackathon_id(hackathon_id):
        if hackathon_id == "missing-event":
            return {"teams": []}
        return {"teams": [dict(TEAM_SUBMITTED), dict(TEAM_DRAFT)]}

    def submit_project(propel_user_id, team_id, admin=False):
        state["submit_calls"].append((propel_user_id, team_id, admin))
        team = TEAMS_BY_ID.get(team_id)
        if team is None:
            return {"error": "Team not found"}, 404
        if team["project_submission_status"] in ("submitted", "late"):
            return {"success": True, "already_submitted": True, "team": team}, 200
        missing = [
            field
            for field in ("project_tagline", "project_story")
            if not (team.get(field) or "").strip()
        ]
        if missing:
            return {"error": "incomplete", "missing": missing}, 400
        return {"success": True, "team": team, "status": "late"}, 200

    def get_github_activity(org_name, repo_name):
        if org_name == "opportunity-hack" and repo_name == "TeamRocket-FoodBank":
            return {
                "success": True,
                "repo": "opportunity-hack/TeamRocket-FoodBank",
                "commits": 42,
                "contributors": ["alice", "bob"],
                "open_prs": 3,
            }, 200
        return {"error": "repo_not_found"}, 404

    monkeypatch.setitem(
        sys.modules,
        "services.teams_service",
        _stub_module("services.teams_service", get_team_admin=get_team_admin),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.teams.teams_service",
        _stub_module(
            "api.teams.teams_service",
            get_teams_by_hackathon_id=get_teams_by_hackathon_id,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.submissions.submissions_service",
        _stub_module(
            "api.submissions.submissions_service",
            SUBMITTED_STATUSES={"submitted", "late"},
            REQUIRED_SUBMIT_FIELDS=("project_tagline", "project_story"),
            submit_project=submit_project,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.github.github_service",
        _stub_module(
            "api.github.github_service", get_github_activity=get_github_activity
        ),
    )
    monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
    import api.mcp.tools as tools_pkg
    from api.mcp import registry

    # Re-importing the tool module re-runs its @register_tool decorators,
    # which raise on a duplicate name — so clear the registry first and
    # restore it afterwards (same save/clear/restore as the protocol
    # tests' fixture).
    saved_registry = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    monkeypatch.setattr(tools_pkg, "_registered", False)
    sys.modules.pop(TOOL_MODULE, None)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client(), state
    sys.modules.pop(TOOL_MODULE, None)
    sys.modules.pop(VIEWS, None)
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved_registry)


def _call(client, name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    body = {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}
    response = client.post("/api/mcp", json=body, headers=HEADERS)
    assert response.status_code == 200
    return response.json


def _payload(response):
    assert "result" in response, response
    result = response["result"]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def test_team_status_submitted(client):
    http, _state = client
    payload = _payload(_call(http, "completion_team_status", {"team_id": "team-1"}))
    assert payload["success"] is True
    assert payload["project_submission_status"] == "submitted"
    assert payload["submitted"] is True
    assert payload["project_submitted_at"] == "2026-11-15T10:00:00+00:00"
    assert payload["missing_submit_fields"] == []
    assert payload["slack_channel"] == "team-rocket"
    assert payload["github_links"][0]["name"] == "TeamRocket-FoodBank"


def test_team_status_draft_lists_missing_fields(client):
    http, _state = client
    payload = _payload(_call(http, "completion_team_status", {"team_id": "team-2"}))
    assert payload["project_submission_status"] == "draft"
    assert payload["submitted"] is False
    assert payload["missing_submit_fields"] == ["project_tagline"]


def test_team_status_unknown_team_is_error(client):
    http, _state = client
    response = _call(http, "completion_team_status", {"team_id": "nope"})
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "team_not_found"


def test_event_status_rollup(client):
    http, _state = client
    payload = _payload(
        _call(http, "completion_event_status", {"hackathon_id": "event-1"})
    )
    assert payload["success"] is True
    assert payload["total_teams"] == 2
    assert payload["submitted"] == 1
    assert payload["draft"] == 1
    rows = {row["team_id"]: row for row in payload["teams"]}
    assert rows["team-1"]["submitted"] is True
    assert rows["team-2"]["submitted"] is False
    assert rows["team-2"]["missing_submit_fields"] == ["project_tagline"]


def test_event_status_empty_event(client):
    http, _state = client
    payload = _payload(
        _call(http, "completion_event_status", {"hackathon_id": "missing-event"})
    )
    assert payload["total_teams"] == 0
    assert payload["submitted"] == 0
    assert payload["draft"] == 0
    assert payload["teams"] == []


def test_mark_submitted_with_confirm_calls_service_as_admin(client):
    http, state = client
    payload = _payload(
        _call(
            http,
            "completion_mark_submitted",
            {"team_id": "team-3", "confirm": True},
        )
    )
    assert payload["success"] is True
    assert payload["status"] == "late"
    assert state["submit_calls"] == [("fake-propel-uuid", "team-3", True)]


def test_mark_submitted_already_submitted_is_idempotent(client):
    http, state = client
    payload = _payload(
        _call(
            http,
            "completion_mark_submitted",
            {"team_id": "team-1", "confirm": True},
        )
    )
    assert payload["already_submitted"] is True
    assert state["submit_calls"] == [("fake-propel-uuid", "team-1", True)]


def test_mark_submitted_without_confirm_is_refused(client):
    http, state = client
    response = _call(http, "completion_mark_submitted", {"team_id": "team-2"})
    assert response["error"]["code"] == -32602
    assert state["submit_calls"] == []


def test_mark_submitted_incomplete_passes_through_as_is_error(client):
    http, _state = client
    # team-2 is a draft missing its tagline; the service's 400 incomplete
    # passes through unchanged as an MCP isError result.
    response = _call(
        http,
        "completion_mark_submitted",
        {"team_id": "team-2", "confirm": True},
    )
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "incomplete"
    assert payload["missing"] == ["project_tagline"]


def test_mark_submitted_unknown_team_passes_through_as_is_error(client):
    http, _state = client
    response = _call(
        http,
        "completion_mark_submitted",
        {"team_id": "nope", "confirm": True},
    )
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "Team not found"


def test_github_activity_happy_path(client):
    http, _state = client
    payload = _payload(
        _call(http, "completion_github_activity", {"team_id": "team-1"})
    )
    assert payload["success"] is True
    assert payload["repo"] == "opportunity-hack/TeamRocket-FoodBank"
    assert payload["commits"] == 42
    assert payload["open_prs"] == 3


def test_github_activity_team_without_repo(client):
    http, _state = client
    response = _call(http, "completion_github_activity", {"team_id": "team-2"})
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "no_github_repo"


def test_github_activity_repo_not_found_passes_through(client):
    http, _state = client
    # team-4's stored URL parses to a repo the service does not know;
    # its 404 repo_not_found passes through as an MCP isError result.
    response = _call(http, "completion_github_activity", {"team_id": "team-4"})
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "repo_not_found"


def test_non_admin_caller_is_denied(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "common.auth", _denying_permissions_auth_module()
    )
    import api.mcp.tools as tools_pkg
    from api.mcp import registry

    saved_registry = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    monkeypatch.setattr(tools_pkg, "_registered", False)
    sys.modules.pop(TOOL_MODULE, None)
    sys.modules.pop(VIEWS, None)
    # Service stubs are not needed: the dispatcher 403s before any
    # handler runs — but the tool module still imports their names, so
    # the stubs must carry the attributes the import binds.
    monkeypatch.setitem(
        sys.modules,
        "services.teams_service",
        _stub_module(
            "services.teams_service", get_team_admin=lambda team_id: None
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.teams.teams_service",
        _stub_module(
            "api.teams.teams_service",
            get_teams_by_hackathon_id=lambda hackathon_id: {"teams": []},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.submissions.submissions_service",
        _stub_module(
            "api.submissions.submissions_service",
            SUBMITTED_STATUSES={"submitted", "late"},
            REQUIRED_SUBMIT_FIELDS=("project_tagline", "project_story"),
            submit_project=lambda *a, **k: ({"success": True}, 200),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.github.github_service",
        _stub_module(
            "api.github.github_service",
            get_github_activity=lambda o, r: ({"success": True}, 200),
        ),
    )
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    try:
        response = _call(
            app.test_client(),
            "completion_team_status",
            {"team_id": "team-1"},
        )
        assert response["error"]["code"] == -32001
    finally:
        sys.modules.pop(TOOL_MODULE, None)
        sys.modules.pop(VIEWS, None)
        registry.TOOL_REGISTRY.clear()
        registry.TOOL_REGISTRY.update(saved_registry)


def test_tools_list_includes_completion_tools(client):
    http, _state = client
    response = http.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    names = [entry["name"] for entry in response.json["result"]["tools"]]
    assert names == [
        "completion_event_status",
        "completion_github_activity",
        "completion_mark_submitted",
        "completion_team_status",
    ]
