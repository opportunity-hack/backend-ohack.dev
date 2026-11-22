"""Tool tests for the reimbursement MCP tools.

The service layer is stubbed at the module boundary (the pattern in
``api/mcp/README.md``): the stub goes into ``sys.modules`` via
``monkeypatch.setitem`` BEFORE ``api.mcp.mcp_views`` is imported, and
``api.mcp.tools._registered`` is reset + the tool module is popped so
it re-imports against the stub. These tests prove dispatch, auth,
validation, and error mapping — not Firestore behavior, which the
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

SERVICE = "api.reimbursements.reimbursements_service"
TOOLS_MODULE = "api.mcp.tools.reimbursements_tools"
VIEWS = "api.mcp.mcp_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}


def _make_service_stub():
    # pylint: disable=no-member
    # The stub is a bare ModuleType whose attributes (results, call log)
    # are set dynamically below; pylint cannot see them.
    stub = types.ModuleType(SERVICE)
    stub.STATUSES = (
        "submitted", "under_review", "needs_info",
        "approved", "denied", "paid",
    )
    stub.calls = {"list": [], "get": [], "review": []}
    stub.list_result = (
        {
            "data": [
                {"id": "doc-1", "status": "submitted", "amount_cents": 4200}
            ],
            "count": 1,
        },
        200,
    )
    stub.get_result = (
        {
            "id": "doc-1",
            "status": "submitted",
            "submitter_email": "claimer@example.com",
            "amount_cents": 4200,
        },
        200,
    )
    stub.review_result = (
        {"success": True, "id": "doc-1", "status": "approved"},
        200,
    )

    def admin_list_reimbursements(status=None, event_id=None, limit=50, offset=0):
        stub.calls["list"].append(
            {
                "status": status,
                "event_id": event_id,
                "limit": limit,
                "offset": offset,
            }
        )
        return stub.list_result

    def admin_get_reimbursement(doc_id):
        stub.calls["get"].append({"doc_id": doc_id})
        return stub.get_result

    def admin_review_reimbursement(doc_id, data, reviewer):
        stub.calls["review"].append(
            {"doc_id": doc_id, "data": dict(data), "reviewer": dict(reviewer)}
        )
        return stub.review_result

    stub.admin_list_reimbursements = admin_list_reimbursements
    stub.admin_get_reimbursement = admin_get_reimbursement
    stub.admin_review_reimbursement = admin_review_reimbursement
    return stub


def _non_admin_auth_module():
    """Passthrough gate, but the caller holds no tool permission."""
    auth = passthrough_auth_module()
    auth.user_has_any_permission = lambda user, perms: False
    return auth


@pytest.fixture
def harness(monkeypatch):
    stub = _make_service_stub()

    def _load(auth_module=None):
        monkeypatch.setitem(
            sys.modules, "common.auth",
            auth_module or passthrough_auth_module(),
        )
        monkeypatch.setitem(sys.modules, SERVICE, stub)
        # `from api.reimbursements import reimbursements_service` resolves
        # via the parent package attribute when the real module was already
        # imported (by an earlier test file), bypassing the sys.modules
        # stub — point the attribute at the stub too.
        import api.reimbursements as reimbursements_pkg

        monkeypatch.setattr(
            reimbursements_pkg, "reimbursements_service", stub, raising=False
        )
        import api.mcp.tools as tools_pkg
        from api.mcp import registry

        # Re-importing the tool module would raise "already registered"
        # unless the registry is cleared first.
        registry.TOOL_REGISTRY.clear()
        monkeypatch.setattr(tools_pkg, "_registered", False)
        sys.modules.pop(TOOLS_MODULE, None)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return app.test_client()

    yield _load, stub
    sys.modules.pop(VIEWS, None)


def _call(client, name, arguments, request_id=1):
    return client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=HEADERS,
    )


def _tool_payload(response):
    """(result, parsed payload) for a tools/call response."""
    result = response.json["result"]
    return result, json.loads(result["content"][0]["text"])


def test_list_happy_path(harness):
    client, stub = harness
    client = client()
    result, payload = _tool_payload(
        _call(
            client,
            "reimbursement_list",
            {"status": "submitted", "event_id": "evt-1", "limit": 10, "offset": 5},
        )
    )
    assert result["isError"] is False
    assert payload["count"] == 1
    assert payload["data"][0]["id"] == "doc-1"
    assert stub.calls["list"] == [
        {"status": "submitted", "event_id": "evt-1", "limit": 10, "offset": 5}
    ]


def test_list_defaults_to_limit_50_offset_0(harness):
    client, stub = harness
    client = client()
    result, _ = _tool_payload(_call(client, "reimbursement_list", {}))
    assert result["isError"] is False
    assert stub.calls["list"] == [
        {"status": None, "event_id": None, "limit": 50, "offset": 0}
    ]


def test_list_rejects_status_outside_enum(harness):
    client, stub = harness
    client = client()
    response = _call(client, "reimbursement_list", {"status": "bogus"})
    assert response.json["error"]["code"] == -32602
    assert stub.calls["list"] == []


def test_get_happy_path(harness):
    client, stub = harness
    client = client()
    result, payload = _tool_payload(
        _call(client, "reimbursement_get", {"reimbursement_id": "doc-1"})
    )
    assert result["isError"] is False
    assert payload["id"] == "doc-1"
    assert payload["submitter_email"] == "claimer@example.com"
    assert stub.calls["get"] == [{"doc_id": "doc-1"}]


def test_get_not_found_passes_through_as_is_error(harness):
    client, stub = harness
    stub.get_result = (
        {"success": False, "error": "not_found",
         "message": "Reimbursement not found"},
        404,
    )
    client = client()
    result, payload = _tool_payload(
        _call(client, "reimbursement_get", {"reimbursement_id": "doc-missing"})
    )
    assert result["isError"] is True
    assert payload["error"] == "not_found"


def test_review_happy_path_with_confirm(harness):
    client, stub = harness
    client = client()
    result, payload = _tool_payload(
        _call(
            client,
            "reimbursement_review",
            {
                "reimbursement_id": "doc-1",
                "status": "approved",
                "reviewNote": "Receipt checks out",
                "confirm": True,
            },
        )
    )
    assert result["isError"] is False
    assert payload["status"] == "approved"
    assert len(stub.calls["review"]) == 1
    call = stub.calls["review"][0]
    assert call["doc_id"] == "doc-1"
    # Only the fields the service reads are forwarded.
    assert call["data"] == {"status": "approved", "reviewNote": "Receipt checks out"}
    # Reviewer identity comes from the token, never from arguments.
    assert call["reviewer"] == {
        "propel_id": "fake-propel-uuid",
        "email": "fake@example.com",
        "name": None,
    }


def test_review_forwards_payment_reference_when_paid(harness):
    client, stub = harness
    stub.review_result = (
        {"success": True, "id": "doc-1", "status": "paid"},
        200,
    )
    client = client()
    result, payload = _tool_payload(
        _call(
            client,
            "reimbursement_review",
            {
                "reimbursement_id": "doc-1",
                "status": "paid",
                "paymentReference": "CHK-1234",
                "confirm": True,
            },
        )
    )
    assert result["isError"] is False
    assert payload["status"] == "paid"
    assert stub.calls["review"][0]["data"] == {
        "status": "paid",
        "paymentReference": "CHK-1234",
    }


def test_review_without_confirm_is_refused(harness):
    client, stub = harness
    client = client()
    response = _call(
        client,
        "reimbursement_review",
        {"reimbursement_id": "doc-1", "status": "approved"},
    )
    assert response.json["error"]["code"] == -32602
    assert "confirm" in response.json["error"]["message"]
    assert stub.calls["review"] == []


def test_review_with_confirm_false_is_refused(harness):
    client, stub = harness
    client = client()
    response = _call(
        client,
        "reimbursement_review",
        {"reimbursement_id": "doc-1", "status": "approved", "confirm": False},
    )
    assert response.json["error"]["code"] == -32602
    assert stub.calls["review"] == []


def test_review_requires_status(harness):
    client, stub = harness
    client = client()
    response = _call(
        client,
        "reimbursement_review",
        {"reimbursement_id": "doc-1", "confirm": True},
    )
    assert response.json["error"]["code"] == -32602
    assert stub.calls["review"] == []


def test_review_self_approval_passes_through_as_is_error(harness):
    client, stub = harness
    stub.review_result = (
        {
            "success": False,
            "error": "self_approval",
            "message": "You cannot review your own reimbursement claim",
        },
        403,
    )
    client = client()
    result, payload = _tool_payload(
        _call(
            client,
            "reimbursement_review",
            {
                "reimbursement_id": "doc-1",
                "status": "approved",
                "confirm": True,
            },
        )
    )
    assert result["isError"] is True
    assert payload["error"] == "self_approval"


def test_review_payment_reference_error_passes_through(harness):
    client, stub = harness
    stub.review_result = (
        {
            "success": False,
            "error": "payment_reference_required",
            "message": "Add the payment reference to mark this paid",
        },
        400,
    )
    client = client()
    result, payload = _tool_payload(
        _call(
            client,
            "reimbursement_review",
            {
                "reimbursement_id": "doc-1",
                "status": "paid",
                "confirm": True,
            },
        )
    )
    assert result["isError"] is True
    assert payload["error"] == "payment_reference_required"


def test_non_admin_caller_is_denied_the_tools(harness):
    client, stub = harness
    client = client(_non_admin_auth_module())
    response = _call(
        client,
        "reimbursement_list",
        {"status": "submitted"},
    )
    assert response.json["error"]["code"] == -32001
    assert stub.calls["list"] == []
    listed = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=HEADERS,
    )
    assert listed.json["result"] == {"tools": []}


def test_tools_list_includes_the_three_reimbursement_tools(harness):
    client, stub = harness
    client = client()
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    names = [entry["name"] for entry in response.json["result"]["tools"]]
    assert names == [
        "reimbursement_get",
        "reimbursement_list",
        "reimbursement_review",
    ]
