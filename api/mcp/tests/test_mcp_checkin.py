"""Tools tests for the check-in MCP tools (`api/mcp/tools/checkin.py`).

Route tests through Flask: the check-in service module is stubbed in
``sys.modules`` (only via ``monkeypatch.setitem``) BEFORE importing
``api.mcp.mcp_views``, and ``api.mcp.tools._registered`` is reset + the
tool module popped so it re-imports against the stub (see
``api/mcp/README.md``). The ``common.auth`` stub is the passthrough
flavour with a ``user_has_any_permission`` that mirrors the real one
over the fake user's org member info, so the permission matrix below
exercises the dispatcher's per-tool check, not the endpoint gate.

Permission matrix under test (the point of this PR):

- ``volunteer.admin``: all four tools.
- ``checkin.admin``-only: roster, checked-in, check-in — but NOT reset.
- neither: none of the four.
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
TOOL_MODULE = "api.mcp.tools.checkin"
HEADERS = {"Authorization": "Bearer x", "X-Org-Id": "org-1"}

VOLUNTEER_ADMIN = ("volunteer.admin",)
CHECKIN_ADMIN = ("checkin.admin",)
NEITHER = ()

EVENT = {"event_id": "fall-2026", "type": "hacker"}


def _service_stub():
    svc = types.ModuleType("api.checkin.checkin_service")

    class CheckInError(Exception):
        def __init__(self, status, error, message):
            super().__init__(message)
            self.status, self.error, self.message = status, error, message

    svc.CheckInError = CheckInError
    svc.VOLUNTEER_TYPES = ("mentor", "judge", "volunteer", "hacker", "sponsor")
    svc.MAX_RESET_IDS = 500
    calls = {"roster": [], "checked_in": [], "check_in": [], "reset": []}
    svc.calls = calls

    def get_checkin_roster(event_id, volunteer_type):
        calls["roster"].append((event_id, volunteer_type))
        return [{"id": "v1", "name": "Ada", "checkedIn": False}]

    def get_checked_in(event_id, volunteer_type):
        calls["checked_in"].append((event_id, volunteer_type))
        return [{"id": "v2", "name": "Bo", "checkedIn": True}]

    def check_in_volunteer(event_id, volunteer_type, volunteer_id, propel_id):
        calls["check_in"].append(
            (event_id, volunteer_type, volunteer_id, propel_id)
        )
        if volunteer_id == "missing":
            raise CheckInError(404, "not_found", "No volunteer with that id.")
        if volunteer_id == "other-event":
            raise CheckInError(
                409,
                "wrong_event",
                "This code belongs to a different event or role.",
            )
        if volunteer_id == "v-repeat":
            return {
                "data": {"id": volunteer_id, "checkedIn": True},
                "already_checked_in": True,
            }
        return {
            "data": {"id": volunteer_id, "checkedIn": True},
            "already_checked_in": False,
        }

    def reset_checkins(event_id, volunteer_type, propel_id, volunteer_ids=None):
        calls["reset"].append(
            (event_id, volunteer_type, propel_id, volunteer_ids)
        )
        if volunteer_ids is None:
            return {"reset": 2, "ids": ["v1", "v2"], "skipped": []}
        return {
            "reset": len(volunteer_ids),
            "ids": list(volunteer_ids),
            "skipped": [],
        }

    svc.get_checkin_roster = get_checkin_roster
    svc.get_checked_in = get_checked_in
    svc.check_in_volunteer = check_in_volunteer
    svc.reset_checkins = reset_checkins
    return svc


def _user_with_perms(perms):
    return types.SimpleNamespace(
        user_id="fake-propel-uuid",
        email="fake@example.com",
        name="Fake Admin",
        org_id_to_org_member_info={
            "org-1": types.SimpleNamespace(
                user_has_permission=lambda p: p in perms,
                user_permissions=list(perms),
            )
        },
    )


def _has_permission(user, permissions):
    # Mirrors common.auth.user_has_any_permission for the fake user shape.
    if not user or not getattr(user, "user_id", None):
        return False
    org_map = getattr(user, "org_id_to_org_member_info", None) or {}
    for org_info in org_map.values():
        try:
            if any(org_info.user_has_permission(p) for p in permissions):
                return True
        except Exception:
            granted = getattr(org_info, "user_permissions", None) or []
            if any(p in granted for p in permissions):
                return True
    return False


@pytest.fixture
def load(monkeypatch):
    def _load(perms):
        stub = _service_stub()
        monkeypatch.setitem(sys.modules, "api.checkin.checkin_service", stub)
        # `from api.checkin import checkin_service` prefers the package
        # attribute (set when another test imported the real service).
        import api.checkin as pkg

        monkeypatch.setattr(pkg, "checkin_service", stub, raising=False)
        auth_module = passthrough_auth_module(user=_user_with_perms(perms))
        auth_module.user_has_any_permission = _has_permission
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        import api.mcp.registry as registry

        monkeypatch.setattr(registry, "TOOL_REGISTRY", {})
        import api.mcp.tools as tools_pkg

        monkeypatch.setattr(tools_pkg, "_registered", False)
        sys.modules.pop(TOOL_MODULE, None)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return stub, app.test_client()

    yield _load
    sys.modules.pop(TOOL_MODULE, None)
    sys.modules.pop(VIEWS, None)


def _call(client, name, arguments, request_id=1):
    r = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    return r.json


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def _list_names(client):
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    return [entry["name"] for entry in r.json["result"]["tools"]]


def _checkin_args(confirm=True):
    return dict(EVENT, volunteer_id="v1", confirm=confirm)


# --- happy paths -----------------------------------------------------------


def test_roster_returns_data_and_count(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(client, "checkin_roster", dict(EVENT))
    assert response["result"]["isError"] is False
    payload = _payload(response)
    assert payload["data"] == [{"id": "v1", "name": "Ada", "checkedIn": False}]
    assert payload["count"] == 1
    assert stub.calls["roster"] == [("fall-2026", "hacker")]


def test_checked_in_returns_data_and_count(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(client, "checkin_checked_in", dict(EVENT))
    assert response["result"]["isError"] is False
    payload = _payload(response)
    assert payload["data"] == [{"id": "v2", "name": "Bo", "checkedIn": True}]
    assert payload["count"] == 1
    assert stub.calls["checked_in"] == [("fall-2026", "hacker")]


def test_check_in_with_confirm_marks_checked_in(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(client, "checkin_check_in", _checkin_args())
    assert response["result"]["isError"] is False
    payload = _payload(response)
    assert payload["data"]["checkedIn"] is True
    assert payload["already_checked_in"] is False
    # Caller identity comes from the token, never from the arguments.
    assert stub.calls["check_in"] == [
        ("fall-2026", "hacker", "v1", "fake-propel-uuid")
    ]


def test_check_in_repeat_is_idempotent(load):
    _, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client, "checkin_check_in", dict(EVENT, volunteer_id="v-repeat", confirm=True)
    )
    assert response["result"]["isError"] is False
    assert _payload(response)["already_checked_in"] is True


def test_check_in_not_found_passes_through_as_is_error(load):
    _, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client, "checkin_check_in", dict(EVENT, volunteer_id="missing", confirm=True)
    )
    assert response["result"]["isError"] is True
    payload = _payload(response)
    assert payload["error"] == "not_found"


def test_check_in_wrong_event_passes_through_as_is_error(load):
    _, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client,
        "checkin_check_in",
        dict(EVENT, volunteer_id="other-event", confirm=True),
    )
    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "wrong_event"


def test_check_in_requires_confirm(load):
    _, client = load(VOLUNTEER_ADMIN)
    assert _call(client, "checkin_check_in", _checkin_args(confirm=False))[
        "error"
    ]["code"] == -32602
    args = dict(EVENT, volunteer_id="v1")
    assert _call(client, "checkin_check_in", args)["error"]["code"] == -32602


def test_check_in_rejects_bad_type_and_missing_volunteer(load):
    _, client = load(VOLUNTEER_ADMIN)
    assert _call(
        client,
        "checkin_check_in",
        dict(EVENT, type="staff", volunteer_id="v1", confirm=True),
    )["error"]["code"] == -32602
    assert _call(client, "checkin_check_in", dict(EVENT, confirm=True))[
        "error"
    ]["code"] == -32602


# --- reset -----------------------------------------------------------------


def test_reset_with_confirm_and_ids_as_volunteer_admin(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client,
        "checkin_reset",
        dict(EVENT, volunteer_ids=["v1", "v2"], confirm=True),
    )
    assert response["result"]["isError"] is False
    payload = _payload(response)
    assert payload["reset"] == 2
    assert payload["ids"] == ["v1", "v2"]
    assert stub.calls["reset"] == [
        ("fall-2026", "hacker", "fake-propel-uuid", ["v1", "v2"])
    ]


def test_reset_all_scope_passes_volunteer_ids_none(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(client, "checkin_reset", dict(EVENT, **{"all": True, "confirm": True}))
    assert response["result"]["isError"] is False
    assert _payload(response)["reset"] == 2
    assert stub.calls["reset"][0][3] is None


def test_reset_requires_confirm(load):
    _, client = load(VOLUNTEER_ADMIN)
    assert _call(
        client, "checkin_reset", dict(EVENT, volunteer_ids=["v1"])
    )["error"]["code"] == -32602
    assert _call(
        client,
        "checkin_reset",
        dict(EVENT, volunteer_ids=["v1"], confirm=False),
    )["error"]["code"] == -32602


def test_reset_without_scope_is_missing_scope(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(client, "checkin_reset", dict(EVENT, confirm=True))
    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "missing_scope"
    assert stub.calls["reset"] == []


def test_reset_empty_ids_is_missing_scope(load):
    _, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client, "checkin_reset", dict(EVENT, volunteer_ids=[], confirm=True)
    )
    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "missing_scope"


def test_reset_too_many_ids_is_capped(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client,
        "checkin_reset",
        dict(EVENT, volunteer_ids=["v%d" % i for i in range(501)], confirm=True),
    )
    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "too_many_ids"
    assert stub.calls["reset"] == []


def test_reset_invalid_ids_are_rejected(load):
    stub, client = load(VOLUNTEER_ADMIN)
    response = _call(
        client,
        "checkin_reset",
        dict(EVENT, volunteer_ids=["v1", 7], confirm=True),
    )
    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "invalid_ids"
    assert stub.calls["reset"] == []


# --- per-tool permissions ---------------------------------------------------


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("checkin_roster", dict(EVENT)),
        ("checkin_checked_in", dict(EVENT)),
        ("checkin_check_in", _checkin_args()),
    ],
)
def test_checkin_admin_can_call_the_three_desk_tools(load, name, arguments):
    _, client = load(CHECKIN_ADMIN)
    response = _call(client, name, arguments)
    assert "error" not in response
    assert response["result"]["isError"] is False


def test_checkin_admin_is_forbidden_from_reset(load):
    stub, client = load(CHECKIN_ADMIN)
    response = _call(
        client, "checkin_reset", dict(EVENT, volunteer_ids=["v1"], confirm=True)
    )
    assert response["error"]["code"] == -32001
    assert stub.calls["reset"] == []
    # The permission check precedes the confirm check.
    response = _call(client, "checkin_reset", dict(EVENT, volunteer_ids=["v1"]))
    assert response["error"]["code"] == -32001


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("checkin_roster", dict(EVENT)),
        ("checkin_checked_in", dict(EVENT)),
        ("checkin_check_in", _checkin_args()),
        ("checkin_reset", dict(EVENT, volunteer_ids=["v1"], confirm=True)),
    ],
)
def test_caller_with_neither_permission_is_forbidden_from_all(
    load, name, arguments
):
    _, client = load(NEITHER)
    assert _call(client, name, arguments)["error"]["code"] == -32001


def test_tools_list_filtered_for_checkin_admin(load):
    _, client = load(CHECKIN_ADMIN)
    assert _list_names(client) == [
        "checkin_check_in",
        "checkin_checked_in",
        "checkin_roster",
    ]


def test_tools_list_shows_all_four_to_volunteer_admin(load):
    _, client = load(VOLUNTEER_ADMIN)
    assert _list_names(client) == [
        "checkin_check_in",
        "checkin_checked_in",
        "checkin_reset",
        "checkin_roster",
    ]


def test_tools_list_empty_for_caller_with_neither_permission(load):
    _, client = load(NEITHER)
    assert _list_names(client) == []
