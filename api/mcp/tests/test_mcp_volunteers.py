"""Tests for the volunteers_* MCP tools.

The service layer (``services.volunteers_service``) is stubbed in
``sys.modules`` via ``monkeypatch.setitem`` BEFORE importing
``api.mcp.mcp_views``, and ``api.mcp.tools._registered`` is reset +
the tool module and ``mcp_views`` are popped from ``sys.modules`` so
the tool module re-imports against the stub (see ``api/mcp/README.md``).
The registry is cleared for the same reason (re-import would otherwise
raise "already registered") and restored afterwards, so these tests do
not leak tools into the protocol/auth test files.
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

VIEWS = "api.mcp.mcp_views"
MODULE = "api.mcp.tools.volunteers_tools"
SERVICE = "services.volunteers_service"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}
ADMIN_ID = "fake-propel-uuid"

ALL_TOOLS = [
    "volunteers_bulk_refund_deposits",
    "volunteers_list_by_event",
    "volunteers_message",
    "volunteers_refund_deposit",
    "volunteers_resend_status",
    "volunteers_select",
]


class _StubService(types.ModuleType):
    """A recording stand-in for ``services.volunteers_service``.

    A ``ModuleType`` subclass (rather than a bare module with attributes
    bolted on) so the ``calls``/``state`` attributes are visible to
    pylint; the stub functions close over the instance.
    """

    def __init__(self):
        super().__init__(SERVICE)
        self.calls = {}
        # Per-test behaviour switches: tests mutate these to drive the
        # not-found / service-error paths.
        self.state = {
            "select_result": {
                "id": "vol-1",
                "isSelected": True,
                "updated_by": ADMIN_ID,
            },
            "refund_error": None,
            "message_result": {
                "success": True,
                "volunteer_id": "vol-1",
                "volunteer_email": "hacker@example.com",
                "delivery_status": {"slack_sent": False, "email_sent": True},
            },
            "resend_status_result": {
                "success": True,
                "statuses": {"re_1": {"id": "re_1", "last_event": "delivered"}},
            },
            "resend_list_result": {
                "success": True,
                "emails_by_recipient": {"a@example.com": [{"id": "re_1"}]},
                "total_fetched": 1,
                "syncing": False,
            },
        }

        def get_volunteers_by_event(event_id, volunteer_type, page=1,
                                    limit=20, selected=None):
            self.calls["list"] = {
                "event_id": event_id,
                "type": volunteer_type,
                "page": page,
                "limit": limit,
                "selected": selected,
            }
            return [
                {"id": "vol-1", "name": "Ada", "isSelected": True},
                {"id": "vol-2", "name": "Bo", "isSelected": False},
            ]

        def update_volunteer_selection(volunteer_id, selected, updated_by):
            self.calls["select"] = {
                "volunteer_id": volunteer_id,
                "selected": selected,
                "updated_by": updated_by,
            }
            return self.state["select_result"]

        def refund_hacker_deposit(volunteer_id, admin_user_id, override=False):
            self.calls["refund"] = {
                "volunteer_id": volunteer_id,
                "admin_user_id": admin_user_id,
                "override": override,
            }
            if self.state["refund_error"] is not None:
                # Pylint sees only the None default in the state dict;
                # tests always store a real exception instance here.
                raise self.state["refund_error"]  # pylint: disable=raising-bad-type
            return {
                "id": volunteer_id,
                "deposit_status": "refunded",
                "deposit_refund_id": "re_123",
                "deposit_refund_amount_cents": 5000,
            }

        def bulk_refund_eligible_hacker_deposits(event_id, admin_user_id):
            self.calls["bulk_refund"] = {
                "event_id": event_id,
                "admin_user_id": admin_user_id,
            }
            return {
                "refunded": [{"id": "vol-1", "amount_cents": 5000}],
                "failed": [],
                "total_amount_cents": 5000,
            }

        def send_volunteer_message(volunteer_id, message, subject,
                                   admin_user_id, admin_user=None,
                                   recipient_type="volunteer",
                                   recipient_id=None, fallback_email=None,
                                   fallback_name=None):
            self.calls["message"] = {
                "volunteer_id": volunteer_id,
                "message": message,
                "subject": subject,
                "admin_user_id": admin_user_id,
                "admin_user_id_attr": getattr(admin_user, "user_id", None),
                "recipient_type": recipient_type,
                "recipient_id": recipient_id,
                "fallback_email": fallback_email,
                "fallback_name": fallback_name,
            }
            return self.state["message_result"]

        def get_resend_email_statuses(email_ids):
            self.calls["resend_status"] = {"email_ids": email_ids}
            return self.state["resend_status_result"]

        def list_all_resend_emails(filter_emails=None, force=False):
            self.calls["resend_list"] = {
                "filter_emails": filter_emails,
                "force": force,
            }
            return self.state["resend_list_result"]

        self.get_volunteers_by_event = get_volunteers_by_event
        self.update_volunteer_selection = update_volunteer_selection
        self.refund_hacker_deposit = refund_hacker_deposit
        self.bulk_refund_eligible_hacker_deposits = (
            bulk_refund_eligible_hacker_deposits
        )
        self.send_volunteer_message = send_volunteer_message
        self.get_resend_email_statuses = get_resend_email_statuses
        self.list_all_resend_emails = list_all_resend_emails


def _make_stub_service():
    return _StubService()


@pytest.fixture
def client(monkeypatch):
    stub = _make_stub_service()
    monkeypatch.setitem(sys.modules, SERVICE, stub)
    monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
    import api.mcp.registry as registry
    import api.mcp.tools as tools_pkg

    saved_registry = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    monkeypatch.setattr(tools_pkg, "_registered", False)
    sys.modules.pop(MODULE, None)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client(), stub
    sys.modules.pop(VIEWS, None)
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved_registry)


@pytest.fixture
def non_admin_client(monkeypatch):
    """Same as ``client`` but the dispatcher sees a caller with no
    admin permission (``user_has_any_permission`` -> False)."""
    stub = _make_stub_service()
    monkeypatch.setitem(sys.modules, SERVICE, stub)
    auth_module = passthrough_auth_module()
    auth_module.user_has_any_permission = lambda user, perms: False
    monkeypatch.setitem(sys.modules, "common.auth", auth_module)
    import api.mcp.registry as registry
    import api.mcp.tools as tools_pkg

    saved_registry = dict(registry.TOOL_REGISTRY)
    registry.TOOL_REGISTRY.clear()
    monkeypatch.setattr(tools_pkg, "_registered", False)
    sys.modules.pop(MODULE, None)
    sys.modules.pop(VIEWS, None)
    views = importlib.import_module(VIEWS)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client(), stub
    sys.modules.pop(VIEWS, None)
    registry.TOOL_REGISTRY.clear()
    registry.TOOL_REGISTRY.update(saved_registry)


def _call(name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": params}


def _post(client, body):
    return client.post("/api/mcp", json=body, headers=HEADERS)


def _payload(response):
    assert "result" in response.json, response.json
    result = response.json["result"]
    return result, json.loads(result["content"][0]["text"])


def test_tools_list_shows_all_six_volunteer_tools(client):
    http_client, _stub = client
    r = _post(http_client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200
    names = [entry["name"] for entry in r.json["result"]["tools"]]
    assert names == ALL_TOOLS


def test_list_by_event_happy_path(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_list_by_event",
        {"event_id": "fall-2026", "type": "hacker", "page": 2, "limit": 5,
         "selected": True},
    ))
    assert r.status_code == 200
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["success"] is True
    assert [v["id"] for v in payload["volunteers"]] == ["vol-1", "vol-2"]
    assert (payload["page"], payload["limit"], payload["total"]) == (2, 5, 2)
    assert stub.calls["list"] == {
        "event_id": "fall-2026", "type": "hacker", "page": 2, "limit": 5,
        "selected": True,
    }


def test_list_by_event_defaults_page_limit_and_no_selected_filter(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_list_by_event",
        {"event_id": "fall-2026", "type": "mentor"},
    ))
    assert r.status_code == 200
    _result, payload = _payload(r)
    assert payload["success"] is True
    assert stub.calls["list"] == {
        "event_id": "fall-2026", "type": "mentor", "page": 1, "limit": 20,
        "selected": None,
    }


def test_list_by_event_rejects_unknown_type(client):
    http_client, _stub = client
    r = _post(http_client, _call(
        "volunteers_list_by_event",
        {"event_id": "fall-2026", "type": "superuser"},
    ))
    assert r.json["error"]["code"] == -32602


def test_select_with_confirm_calls_service_with_token_identity(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_select",
        {"volunteer_id": "vol-1", "selected": True, "confirm": True},
    ))
    assert r.status_code == 200
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["volunteer"]["isSelected"] is True
    # Identity comes from the token (fake-propel-uuid), never arguments.
    assert stub.calls["select"] == {
        "volunteer_id": "vol-1", "selected": True, "updated_by": ADMIN_ID,
    }


def test_select_rejects_non_bool_selected(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_select",
        {"volunteer_id": "vol-1", "selected": "true", "confirm": True},
    ))
    assert r.json["error"]["code"] == -32602
    assert "select" not in stub.calls


def test_select_without_confirm_is_refused(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_select",
        {"volunteer_id": "vol-1", "selected": True},
    ))
    assert r.json["error"]["code"] == -32602
    assert "select" not in stub.calls


def test_select_not_found_is_error_result(client):
    http_client, stub = client
    stub.state["select_result"] = None
    r = _post(http_client, _call(
        "volunteers_select",
        {"volunteer_id": "vol-missing", "selected": True, "confirm": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is True
    assert payload["error"] == "Volunteer not found"


def test_refund_with_confirm_calls_service(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_refund_deposit",
        {"volunteer_id": "vol-1", "confirm": True},
    ))
    assert r.status_code == 200
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["volunteer"]["deposit_refund_id"] == "re_123"
    assert stub.calls["refund"] == {
        "volunteer_id": "vol-1", "admin_user_id": ADMIN_ID, "override": False,
    }


def test_refund_override_passes_through(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_refund_deposit",
        {"volunteer_id": "vol-1", "override": True, "confirm": True},
    ))
    _result, payload = _payload(r)
    assert payload["success"] is True
    assert stub.calls["refund"]["override"] is True


def test_refund_without_confirm_is_refused(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_refund_deposit", {"volunteer_id": "vol-1"},
    ))
    assert r.json["error"]["code"] == -32602
    assert "refund" not in stub.calls


def test_refund_service_error_passes_through_as_is_error(client):
    http_client, stub = client
    stub.state["refund_error"] = ValueError(
        "Deposit has already been refunded"
    )
    r = _post(http_client, _call(
        "volunteers_refund_deposit",
        {"volunteer_id": "vol-1", "confirm": True},
    ))
    assert r.status_code == 200
    result, payload = _payload(r)
    assert result["isError"] is True
    assert payload["error"] == "Deposit has already been refunded"
    assert payload["success"] is False


def test_refund_stripe_error_passes_through_as_is_error(client):
    http_client, stub = client
    stub.state["refund_error"] = RuntimeError("Stripe refund failed: boom")
    r = _post(http_client, _call(
        "volunteers_refund_deposit",
        {"volunteer_id": "vol-1", "confirm": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is True
    assert payload["error"] == "Stripe refund failed: boom"


def test_bulk_refund_with_confirm(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_bulk_refund_deposits",
        {"event_id": "fall-2026", "confirm": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["total_amount_cents"] == 5000
    assert len(payload["refunded"]) == 1
    assert stub.calls["bulk_refund"] == {
        "event_id": "fall-2026", "admin_user_id": ADMIN_ID,
    }


def test_bulk_refund_without_confirm_is_refused(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_bulk_refund_deposits", {"event_id": "fall-2026"},
    ))
    assert r.json["error"]["code"] == -32602
    assert "bulk_refund" not in stub.calls


def test_message_with_confirm_calls_service(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_message",
        {"volunteer_id": "vol-1", "message": "See you Saturday",
         "confirm": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["success"] is True
    call = stub.calls["message"]
    assert call["volunteer_id"] == "vol-1"
    assert call["message"] == "See you Saturday"
    assert call["subject"] == "Message from Opportunity Hack Team"
    assert call["recipient_type"] == "volunteer"
    assert call["recipient_id"] == "vol-1"
    assert call["admin_user_id"] == ADMIN_ID
    assert call["admin_user_id_attr"] == ADMIN_ID


def test_message_without_confirm_is_refused(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_message",
        {"volunteer_id": "vol-1", "message": "See you Saturday"},
    ))
    assert r.json["error"]["code"] == -32602
    assert "message" not in stub.calls


def test_message_not_found_is_error_result(client):
    http_client, stub = client
    stub.state["message_result"] = {
        "success": False,
        "error": "Volunteer not found",
        "volunteer_id": "vol-missing",
    }
    r = _post(http_client, _call(
        "volunteers_message",
        {"volunteer_id": "vol-missing", "message": "hi", "confirm": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is True
    assert payload["error"] == "Volunteer not found"


def test_resend_status_with_email_ids(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_resend_status", {"email_ids": ["re_1", "re_2"]},
    ))
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["statuses"]["re_1"]["last_event"] == "delivered"
    assert stub.calls["resend_status"] == {"email_ids": ["re_1", "re_2"]}
    assert "resend_list" not in stub.calls


def test_resend_status_without_email_ids_lists_index(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_resend_status",
        {"emails": ["a@example.com"], "force": True},
    ))
    result, payload = _payload(r)
    assert result["isError"] is False
    assert payload["total_fetched"] == 1
    assert stub.calls["resend_list"] == {
        "filter_emails": ["a@example.com"], "force": True,
    }
    assert "resend_status" not in stub.calls


def test_resend_status_empty_email_ids_is_rejected(client):
    http_client, stub = client
    r = _post(http_client, _call(
        "volunteers_resend_status", {"email_ids": []},
    ))
    result, payload = _payload(r)
    assert result["isError"] is True
    assert payload["error"] == "email_ids array is required"


def test_non_admin_caller_is_denied(non_admin_client):
    http_client, stub = non_admin_client
    r = _post(http_client, _call(
        "volunteers_list_by_event",
        {"event_id": "fall-2026", "type": "hacker"},
    ))
    assert r.json["error"]["code"] == -32001
    assert "list" not in stub.calls
    # And tools/list hides every volunteer tool from them.
    r = _post(http_client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r.json["result"] == {"tools": []}
