"""Tool tests for the meet_* MCP tools.

The meet service module is stubbed in ``sys.modules`` (only via
``monkeypatch.setitem``) BEFORE importing ``api.mcp.mcp_views``, and
``api.mcp.tools._registered`` is reset + the tool module and the views
module are popped from ``sys.modules`` so the tool module re-imports
against the stub (see ``api/mcp/README.md``). These tests prove dispatch,
auth, validation, and error mapping — not Firestore behavior, which the
service layer's own tests cover.
"""
import importlib
import json
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module
from api.mcp import registry

VIEWS = "api.mcp.mcp_views"
TOOL_MODULE = "api.mcp.tools.meet"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}

POLL_OPEN = {
    "code": "abc123",
    "title": "Sprint planning",
    "description": "",
    "duration_minutes": 30,
    "slots": [
        {"id": "s1", "start": "2026-12-01T17:00:00Z"},
        {"id": "s2", "start": "2026-12-02T17:00:00Z"},
    ],
    "status": "open",
    "final_slot_id": None,
    "vote_count": 2,
}
POLL_FINALIZED = dict(
    POLL_OPEN,
    code="xyz789",
    status="finalized",
    final_slot_id="s1",
    meet_link="https://meet.google.com/abc-defg-hij",
    calendar_event_id="ohackmeetdeadbeef",
)
VOTE = {
    "id": "vote1",
    "name": "Ada",
    "email": "ada@example.com",
    "choices": {"s1": "yes", "s2": "no"},
}

MEET_TOOL_NAMES = [
    "meet_create_poll",
    "meet_delete_poll",
    "meet_delete_vote",
    "meet_finalize_poll",
    "meet_get_poll",
    "meet_list_polls",
    "meet_update_poll",
]


class _StubMeetService(types.ModuleType):
    """A ``ModuleType`` subclass for the meet-service stub, so the
    recording attributes (``calls``, ``overrides``) are visible to pylint
    and to the tests."""

    def __init__(self):
        super(_StubMeetService, self).__init__("api.meet.meet_service")
        self.calls = []  # type: List[tuple]
        self.overrides = {}  # type: Dict[str, Any]


def _stub_meet_service():
    """A recording stub for ``api.meet.meet_service``.

    ``stub.calls`` collects ``(function_name, args_tuple)``; ``stub.overrides``
    maps a function name to the ``(payload, status)`` it should return
    instead of its canned success.
    """
    stub = _StubMeetService()

    def _answer(name, default):
        if name in stub.overrides:
            return stub.overrides[name]
        return default

    def admin_list_polls():
        stub.calls.append(("admin_list_polls", ()))
        return _answer(
            "admin_list_polls",
            (
                {
                    "data": [dict(POLL_OPEN), dict(POLL_FINALIZED)],
                    "calendar_configured": True,
                    "calendar_organizer": "meetings@ohack.org",
                },
                200,
            ),
        )

    def admin_get_poll(code):
        stub.calls.append(("admin_get_poll", (code,)))
        if code == "missing":
            return _answer(
                "admin_get_poll",
                ({"success": False, "error": "not_found", "message": "Poll not found"}, 404),
            )
        return _answer(
            "admin_get_poll",
            (
                {
                    "poll": dict(POLL_OPEN),
                    "votes": [dict(VOTE)],
                    "calendar_configured": True,
                    "calendar_organizer": "meetings@ohack.org",
                },
                200,
            ),
        )

    def admin_create_poll(data, admin=None):
        stub.calls.append(("admin_create_poll", (data, admin)))
        poll = dict(POLL_OPEN, code=data.get("code") or "newcode")
        return _answer(
            "admin_create_poll", ({"success": True, "poll": poll}, 201)
        )

    def admin_update_poll(code, data):
        stub.calls.append(("admin_update_poll", (code, data)))
        poll = dict(POLL_OPEN)
        if "status" in data:
            poll["status"] = data["status"]
        if "title" in data:
            poll["title"] = data["title"]
        return _answer(
            "admin_update_poll", ({"success": True, "poll": poll}, 200)
        )

    def admin_delete_poll(code):
        stub.calls.append(("admin_delete_poll", (code,)))
        return _answer("admin_delete_poll", ({"success": True}, 200))

    def admin_delete_vote(code, vote_id):
        stub.calls.append(("admin_delete_vote", (code, vote_id)))
        return _answer("admin_delete_vote", ({"success": True}, 200))

    def admin_finalize_poll(code, data, admin=None):
        stub.calls.append(("admin_finalize_poll", (code, data, admin)))
        return _answer(
            "admin_finalize_poll",
            ({"success": True, "poll": dict(POLL_FINALIZED), "invited": 2}, 200),
        )

    stub.admin_list_polls = admin_list_polls
    stub.admin_get_poll = admin_get_poll
    stub.admin_create_poll = admin_create_poll
    stub.admin_update_poll = admin_update_poll
    stub.admin_delete_poll = admin_delete_poll
    stub.admin_delete_vote = admin_delete_vote
    stub.admin_finalize_poll = admin_finalize_poll
    return stub


def _denying_auth_module():
    """Passthrough auth, except the dispatcher-level permission check
    always fails: a caller with no volunteer.admin permission."""
    stub = passthrough_auth_module()
    stub.user_has_any_permission = lambda user, perms: False
    return stub


@pytest.fixture
def meet_client(monkeypatch):
    """Flask client for /api/mcp with the meet service stubbed."""
    client, stub, saved = _load(monkeypatch, passthrough_auth_module())
    yield client, stub
    _teardown(saved)


@pytest.fixture
def denied_client(monkeypatch):
    """Same, but the caller holds no tool permission."""
    client, stub, saved = _load(monkeypatch, _denying_auth_module())
    yield client, stub
    _teardown(saved)


def _load(monkeypatch, auth_module):
    stub = _stub_meet_service()
    monkeypatch.setitem(sys.modules, "api.meet.meet_service", stub)
    # A from-import binds the parent package's already-set attribute when
    # ``api.meet`` was imported earlier (e.g. by the auth tests, against
    # the real service), so point that attribute at the stub too;
    # monkeypatch restores it after the test.
    if "api.meet" in sys.modules:
        monkeypatch.setattr(sys.modules["api.meet"], "meet_service", stub)
    monkeypatch.setitem(sys.modules, "common.auth", auth_module)
    import api.mcp.tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "_registered", False)
    saved = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    sys.modules.pop(TOOL_MODULE, None)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    return app.test_client(), stub, saved


def _teardown(saved):
    sys.modules.pop(VIEWS, None)
    sys.modules.pop(TOOL_MODULE, None)
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved)


def _call(client, name, arguments, request_id=1):
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    return response.json


def _payload(response):
    assert response["result"]["isError"] is False
    return json.loads(response["result"]["content"][0]["text"])


def _calls_to(stub, name):
    return [args for fn, args in stub.calls if fn == name]


def test_tools_list_shows_the_seven_meet_tools(meet_client):
    client, _stub = meet_client
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    names = [entry["name"] for entry in response.json["result"]["tools"]]
    assert names == MEET_TOOL_NAMES


def test_list_polls_happy_path(meet_client):
    client, stub = meet_client
    payload = _payload(_call(client, "meet_list_polls", {}))
    assert [poll["code"] for poll in payload["data"]] == ["abc123", "xyz789"]
    assert payload["data"][0]["vote_count"] == 2
    assert payload["calendar_configured"] is True
    assert payload["calendar_organizer"] == "meetings@ohack.org"
    assert _calls_to(stub, "admin_list_polls")


def test_list_polls_filters_by_status_client_side(meet_client):
    client, _stub = meet_client
    payload = _payload(_call(client, "meet_list_polls", {"status": "finalized"}))
    assert [poll["code"] for poll in payload["data"]] == ["xyz789"]
    payload = _payload(_call(client, "meet_list_polls", {"status": "closed"}))
    assert payload["data"] == []


def test_list_polls_rejects_unknown_status(meet_client):
    client, _stub = meet_client
    response = _call(client, "meet_list_polls", {"status": "draft"})
    assert response["error"]["code"] == -32602


def test_get_poll_happy_path(meet_client):
    client, stub = meet_client
    payload = _payload(_call(client, "meet_get_poll", {"code": "abc123"}))
    assert payload["poll"]["code"] == "abc123"
    assert payload["votes"][0]["email"] == "ada@example.com"
    assert _calls_to(stub, "admin_get_poll") == [("abc123",)]


def test_get_poll_requires_code(meet_client):
    client, _stub = meet_client
    response = _call(client, "meet_get_poll", {})
    assert response["error"]["code"] == -32602


def test_get_poll_not_found_passes_through_as_is_error(meet_client):
    client, _stub = meet_client
    response = _call(client, "meet_get_poll", {"code": "missing"})
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload == {
        "success": False,
        "error": "not_found",
        "message": "Poll not found",
    }


def test_create_poll_with_confirm_calls_service(meet_client):
    client, stub = meet_client
    payload = _payload(
        _call(
            client,
            "meet_create_poll",
            {
                "title": "Sprint planning",
                "slots": [{"start": "2026-12-01T17:00:00+00:00"}],
                "durationMinutes": 45,
                "confirm": True,
            },
        )
    )
    assert payload["success"] is True
    assert payload["poll"]["code"] == "newcode"
    calls = _calls_to(stub, "admin_create_poll")
    assert len(calls) == 1
    data, admin = calls[0]
    assert data == {
        "title": "Sprint planning",
        "slots": [{"start": "2026-12-01T17:00:00+00:00"}],
        "durationMinutes": 45,
    }
    # confirm is a dispatcher gate, never forwarded to the service.
    assert "confirm" not in data
    # Identity comes from the token, not the arguments.
    assert admin["propel_id"] == "fake-propel-uuid"
    assert admin["email"] == "fake@example.com"


def test_create_poll_without_confirm_is_refused(meet_client):
    client, stub = meet_client
    response = _call(
        client,
        "meet_create_poll",
        {"title": "Sprint planning", "slots": [{"start": "2026-12-01T17:00:00Z"}]},
    )
    assert response["error"]["code"] == -32602
    assert "confirm" in response["error"]["message"]
    assert _calls_to(stub, "admin_create_poll") == []


def test_update_poll_with_confirm_can_close(meet_client):
    client, stub = meet_client
    payload = _payload(
        _call(
            client,
            "meet_update_poll",
            {"code": "abc123", "status": "closed", "confirm": True},
        )
    )
    assert payload["poll"]["status"] == "closed"
    assert _calls_to(stub, "admin_update_poll") == [
        ("abc123", {"status": "closed"})
    ]


def test_update_poll_without_confirm_is_refused(meet_client):
    client, stub = meet_client
    response = _call(
        client, "meet_update_poll", {"code": "abc123", "status": "closed"}
    )
    assert response["error"]["code"] == -32602
    assert _calls_to(stub, "admin_update_poll") == []


def test_delete_poll_with_confirm(meet_client):
    client, stub = meet_client
    payload = _payload(
        _call(client, "meet_delete_poll", {"code": "abc123", "confirm": True})
    )
    assert payload == {"success": True}
    assert _calls_to(stub, "admin_delete_poll") == [("abc123",)]


def test_delete_poll_without_confirm_is_refused(meet_client):
    client, stub = meet_client
    response = _call(client, "meet_delete_poll", {"code": "abc123"})
    assert response["error"]["code"] == -32602
    assert _calls_to(stub, "admin_delete_poll") == []


def test_delete_vote_with_confirm(meet_client):
    client, stub = meet_client
    payload = _payload(
        _call(
            client,
            "meet_delete_vote",
            {"code": "abc123", "voteId": "vote1", "confirm": True},
        )
    )
    assert payload == {"success": True}
    assert _calls_to(stub, "admin_delete_vote") == [("abc123", "vote1")]


def test_delete_vote_without_confirm_is_refused(meet_client):
    client, stub = meet_client
    response = _call(
        client, "meet_delete_vote", {"code": "abc123", "voteId": "vote1"}
    )
    assert response["error"]["code"] == -32602
    assert _calls_to(stub, "admin_delete_vote") == []


def test_finalize_poll_with_confirm(meet_client):
    client, stub = meet_client
    payload = _payload(
        _call(
            client,
            "meet_finalize_poll",
            {
                "code": "abc123",
                "slotId": "s1",
                "sendInvites": True,
                "extraEmails": ["extra@example.com"],
                "confirm": True,
            },
        )
    )
    assert payload["success"] is True
    assert payload["poll"]["status"] == "finalized"
    assert payload["invited"] == 2
    calls = _calls_to(stub, "admin_finalize_poll")
    assert len(calls) == 1
    code, data, admin = calls[0]
    assert code == "abc123"
    assert data == {
        "slotId": "s1",
        "sendInvites": True,
        "extraEmails": ["extra@example.com"],
    }
    assert admin["propel_id"] == "fake-propel-uuid"


def test_finalize_poll_without_confirm_is_refused(meet_client):
    client, stub = meet_client
    response = _call(
        client, "meet_finalize_poll", {"code": "abc123", "slotId": "s1"}
    )
    assert response["error"]["code"] == -32602
    assert _calls_to(stub, "admin_finalize_poll") == []


def test_finalize_already_finalized_passes_through_as_is_error(meet_client):
    client, stub = meet_client
    stub.overrides["admin_finalize_poll"] = (
        {
            "success": False,
            "error": "already_finalized",
            "message": "This meeting is already scheduled",
        },
        409,
    )
    response = _call(
        client,
        "meet_finalize_poll",
        {"code": "xyz789", "slotId": "s1", "confirm": True},
    )
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["error"] == "already_finalized"


def test_non_admin_caller_is_denied(denied_client):
    client, stub = denied_client
    response = _call(
        client, "meet_list_polls", {}, request_id=1
    )
    assert response["error"]["code"] == -32001
    assert _calls_to(stub, "admin_list_polls") == []
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=HEADERS,
    )
    assert response.json["result"] == {"tools": []}
