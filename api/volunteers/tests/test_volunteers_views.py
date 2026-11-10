"""Route-level regression tests for the volunteers blueprint.

Why these exist: propelauth's ``require_org_member_with_permission`` /
``require_user`` decorators do NOT inject the user or org into the view. They
set the request-scoped ``auth_user`` proxy and call the view with only Flask's
URL params. Seven admin routes here were declared as ``def view(user, org, id)``
and therefore raised ``TypeError: missing 2 required positional arguments`` on
every request (Sentry, Sep 2026) — the select route surfaced it first because
PR #280 was the first time its auth gate actually passed. These tests go
through the real Flask dispatch so a signature/URL-param mismatch fails here
instead of in production.
"""
import functools
import importlib
import inspect
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.volunteers.volunteers_views"
FAKE_USER = types.SimpleNamespace(user_id="admin-propel-uuid", email="admin@example.com")


def _passthrough_decorator_factory(*_args, **_kwargs):
    """Mimics propelauth: authenticate, stash the user on the request, call the
    view with ONLY Flask's URL params (no user/org injection)."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@pytest.fixture
def views(monkeypatch):
    """Import the views module against a stubbed ``common.auth`` (the real one
    calls PropelAuth at import time)."""
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough_decorator_factory,
        require_user=_passthrough_decorator_factory(),
        optional_user=_passthrough_decorator_factory(),
    )
    stub.auth_user = LocalProxy(lambda: g.propelauth_current_user)
    stub.getOrgId = lambda req: req.headers.get("X-Org-Id")
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    module = importlib.import_module(VIEWS_MODULE)
    yield module
    sys.modules.pop(VIEWS_MODULE, None)


@pytest.fixture
def app(views):
    flask_app = Flask(__name__)
    flask_app.register_blueprint(views.bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


HEADERS = {"Authorization": "Bearer test", "X-Org-Id": "org-1"}


def test_every_view_signature_matches_its_url_params(app):
    """The whole bug class: a view must accept exactly the URL params Flask
    passes — nothing propelauth is imagined to inject."""
    mismatches = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = inspect.unwrap(app.view_functions[rule.endpoint])
        declared = {
            name
            for name, p in inspect.signature(view).parameters.items()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }
        if declared != set(rule.arguments):
            mismatches.append((rule.rule, sorted(declared), sorted(rule.arguments)))
    assert mismatches == [], f"view params != URL params: {mismatches}"


def test_select_route_calls_service_with_token_identity(views, client, monkeypatch):
    service = MagicMock(return_value={"id": "vol-1", "isSelected": True})
    monkeypatch.setattr(views, "update_volunteer_selection", service)

    res = client.post("/api/admin/volunteer/vol-1/select", json={"selected": True}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    service.assert_called_once_with(volunteer_id="vol-1", selected=True, updated_by=FAKE_USER.user_id)


def test_select_route_rejects_non_boolean(views, client, monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(views, "update_volunteer_selection", service)

    res = client.post("/api/admin/volunteer/vol-1/select", json={"selected": "yes"}, headers=HEADERS)

    assert res.status_code == 400
    service.assert_not_called()


def test_select_route_404s_unknown_volunteer(views, client, monkeypatch):
    monkeypatch.setattr(views, "update_volunteer_selection", MagicMock(return_value=None))

    res = client.post("/api/admin/volunteer/nope/select", json={"selected": False}, headers=HEADERS)

    assert res.status_code == 404


def test_refund_route_uses_token_identity(views, client, monkeypatch):
    refund = MagicMock(return_value={"deposit_refund_id": "re_1", "deposit_refund_amount_cents": 2500})
    monkeypatch.setattr(views, "refund_hacker_deposit", refund)
    monkeypatch.setattr(views, "send_slack_audit", MagicMock())

    res = client.post("/api/admin/hacker/vol-1/refund-deposit", json={"override": False}, headers=HEADERS)

    assert res.status_code == 200, res.get_json()
    refund.assert_called_once_with(volunteer_id="vol-1", admin_user_id=FAKE_USER.user_id, override=False)


def test_admin_list_routes_dispatch(views, client, monkeypatch):
    monkeypatch.setattr(views, "get_volunteers_by_event", MagicMock(return_value=[]))

    for kind in ("mentors", "sponsors", "judges", "volunteers"):
        res = client.get(f"/api/admin/{kind}/evt-1", headers=HEADERS)
        assert res.status_code == 200, (kind, res.get_json())


def test_hacker_submit_returns_400_when_the_service_rejects(views, client, monkeypatch):
    """A wrong eligibility answer / failed reCAPTCHA used to come back as a
    200 "submitted successfully" with the error tucked inside data."""
    monkeypatch.setattr(views, "send_slack_audit", MagicMock())
    monkeypatch.setattr(views, "create_or_update_volunteer",
                        MagicMock(return_value={"error": "You must be 18 or older."}))

    res = client.post("/api/hacker/application/fall-2026/submit",
                      json={"email": "h@example.com", "name": "H"}, headers=HEADERS)

    assert res.status_code == 400
    body = res.get_json()
    assert body["success"] is False
    assert body["message"] == "You must be 18 or older."


def test_hacker_submit_returns_the_saved_doc(views, client, monkeypatch):
    monkeypatch.setattr(views, "send_slack_audit", MagicMock())
    monkeypatch.setattr(views, "create_or_update_volunteer",
                        MagicMock(return_value={"id": "vol-9", "isSelected": True, "status": "approved"}))

    res = client.post("/api/hacker/application/fall-2026/submit",
                      json={"email": "h@example.com", "name": "H"}, headers=HEADERS)

    assert res.status_code == 200
    assert res.get_json()["data"]["isSelected"] is True


# ---------------------------------------------------------------------------
# Mentor self check-in refusals map to readable 403/409 bodies (Nov 2026).

@pytest.mark.parametrize("result,status", [
    ({"success": False, "error": "not_approved", "message": "Your mentor application hasn't been approved yet."}, 403),
    ({"success": False, "error": "checkin_not_open", "message": "opens later", "opens_at": "2026-11-14T00:00:00-07:00"}, 409),
    ({"success": False, "error": "checkin_closed", "message": "closed", "closes_at": "2026-11-15T23:59:59-07:00"}, 409),
])
def test_mentor_checkin_refusals(views, client, monkeypatch, result, status):
    monkeypatch.setattr(views, "mentor_checkin", lambda u, e, t: result)
    r = client.post("/api/mentor/checkin/fall-2026/in", headers=HEADERS, json={})
    assert r.status_code == status
    assert r.json["error"] == result["error"] and r.json["message"] == result["message"]
    for key in ("opens_at", "closes_at"):
        if key in result:
            assert r.json[key] == result[key]


def test_mentor_checkin_status_carries_approval_and_window(views, client, monkeypatch):
    window = {"state": "upcoming", "opens_at": "a", "closes_at": "b"}
    monkeypatch.setattr(views, "get_mentor_checkin_status", lambda u, e: {
        "success": True, "isCheckedIn": False, "checkInTime": None, "timeSlot": None,
        "isSelected": True, "window": window,
    })
    r = client.get("/api/mentor/checkin/fall-2026/status", headers=HEADERS)
    assert r.status_code == 200
    assert r.json["data"]["isSelected"] is True and r.json["data"]["window"] == window
