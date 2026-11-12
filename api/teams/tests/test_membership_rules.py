"""
Class E (reliability plan Nov 2026): team join / leave / create used to check
nothing beyond login — any logged-in user could join any team through the
API. These tests pin the server rules (ported from the frontend's
lib/teamMembershipApi.js) and the route wiring.
"""
import importlib
import os
import sys
import types
from datetime import datetime

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytz
from flask import Flask

from api.teams import membership_rules as rules
from test.common.auth_stubs import passthrough_auth_module

AZ = pytz.timezone("America/Phoenix")
DURING = AZ.localize(datetime(2026, 11, 14, 12, 0))
AFTER = AZ.localize(datetime(2026, 11, 16, 0, 0, 1))
EVENT = {"event_id": "fall-2026", "start_date": "2026-11-14", "end_date": "2026-11-15",
         "timezone": "America/Phoenix", "constraints": {}}
TEAM = {"active": True, "status": "NONPROFIT_SELECTED", "hackathon_event_id": "fall-2026"}


def _rule(action="join", team=None, event=None, approved=True, now=DURING):
    return rules.team_membership_rules(action, team or dict(TEAM), event or dict(EVENT), approved, now=now)


# --- pure rules -------------------------------------------------------------

def test_join_allowed_for_approved_hacker_on_open_team():
    assert _rule() is None


def test_join_refused_for_unapproved_caller():
    assert _rule(approved=False) == rules.NOT_APPROVED_HACKER


def test_join_refused_after_event_end_in_event_timezone():
    assert _rule(now=AFTER) == rules.EVENT_ENDED
    # 23:59 on end_date in Phoenix is still open
    assert _rule(now=AZ.localize(datetime(2026, 11, 15, 23, 59))) is None


def test_join_refused_when_team_changes_paused():
    event = dict(EVENT, constraints={"team_join_enabled": False})
    assert _rule(event=event) == rules.TEAM_JOIN_DISABLED


@pytest.mark.parametrize("active", [False, "False", None])
def test_join_refused_on_inactive_team(active):
    assert _rule(team=dict(TEAM, active=active)) == rules.TEAM_INACTIVE


def test_string_true_counts_as_active():
    assert _rule(team=dict(TEAM, active="True")) is None


@pytest.mark.parametrize("status", sorted(rules.JOINING_DISABLED_STATUSES))
def test_join_refused_on_closed_status(status):
    assert _rule(team=dict(TEAM, status=status)) == rules.TEAM_NOT_JOINABLE


def test_join_order_matches_frontend_event_ended_first():
    # Frontend teamJoinBlockedReason checks eventEnded before anything else.
    team = dict(TEAM, active=False, status="INACTIVE")
    assert _rule(team=team, approved=False, now=AFTER) == rules.EVENT_ENDED


def test_leave_only_checks_event_and_pause():
    team = dict(TEAM, active=False, status="DEPLOYED")
    assert _rule("leave", team=team, approved=False) is None
    assert _rule("leave", now=AFTER) == rules.EVENT_ENDED
    assert _rule("leave", event=dict(EVENT, constraints={"team_join_enabled": False})) == rules.TEAM_JOIN_DISABLED


def test_joining_disabled_statuses_match_frontend_twin():
    # Twin: frontend src/constants/teamStatus.js JOINING_DISABLED_STATUSES
    assert rules.JOINING_DISABLED_STATUSES == {
        "ONBOARDED", "SWAG_RECEIVED", "PROJECT_SUBMITTED", "COMPLETED_DEMO",
        "COMPLETED_HACKATHON", "FOUNDING_ENGINEERS", "COMPLETION_SUPPORT",
        "CATEGORY_WINNER", "DEPLOYED", "NONPROFIT_SIGNOFF", "INACTIVE",
    }


def test_refusal_shape_and_status():
    body, status = rules.refusal(rules.NOT_APPROVED_HACKER)
    assert status == 403 and body["error"] == "not_approved_hacker" and body["message"]
    assert rules.refusal(rules.EVENT_ENDED)[1] == 409
    assert rules.refusal(rules.TEAM_NOT_FOUND)[1] == 404


def test_approved_hacker_requires_isSelected_true(monkeypatch):
    import services.volunteers_service as vs
    monkeypatch.setattr(vs, "find_volunteer_by_caller_identity", lambda u, e, t: {"isSelected": True})
    assert rules.caller_is_approved_hacker("u", "fall-2026") is True
    monkeypatch.setattr(vs, "find_volunteer_by_caller_identity", lambda u, e, t: {"isSelected": False})
    assert rules.caller_is_approved_hacker("u", "fall-2026") is False
    monkeypatch.setattr(vs, "find_volunteer_by_caller_identity", lambda u, e, t: None)
    assert rules.caller_is_approved_hacker("u", "fall-2026") is False

    def boom(*_a):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(vs, "find_volunteer_by_caller_identity", boom)
    assert rules.caller_is_approved_hacker("u", "fall-2026") is False


def test_check_change_admin_bypasses(monkeypatch):
    monkeypatch.setattr(rules, "_load_team", lambda tid: pytest.fail("admin must not be checked"))
    assert rules.check_team_membership_change("u", "t1", "join", admin=True) is None


def test_check_change_missing_team_is_404(monkeypatch):
    monkeypatch.setattr(rules, "_load_team", lambda tid: None)
    assert rules.check_team_membership_change("u", "t1", "join")[1] == 404


def test_check_change_leave_does_not_look_up_application(monkeypatch):
    monkeypatch.setattr(rules, "_load_team", lambda tid: dict(TEAM))
    monkeypatch.setattr(rules, "_load_event", lambda eid: dict(EVENT))
    monkeypatch.setattr(rules, "caller_is_approved_hacker", lambda *a: pytest.fail("not needed for leave"))
    assert rules.check_team_membership_change("u", "t1", "leave", now=DURING) is None


def test_check_queue(monkeypatch):
    monkeypatch.setattr(rules, "caller_is_approved_hacker", lambda u, e: False)
    assert rules.check_team_queue("u", "fall-2026")[1] == 403
    assert rules.check_team_queue("u", "fall-2026", admin=True) is None
    monkeypatch.setattr(rules, "caller_is_approved_hacker", lambda u, e: True)
    assert rules.check_team_queue("u", "fall-2026") is None


# --- route wiring -----------------------------------------------------------

@pytest.fixture
def load(monkeypatch):
    monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
    loaded = []

    def _load(module_name):
        sys.modules.pop(module_name, None)
        views = importlib.import_module(module_name)
        loaded.append(module_name)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app.test_client()

    yield _load
    for name in loaded:
        sys.modules.pop(name, None)


HEADERS = {"Authorization": "Bearer x"}


@pytest.mark.parametrize("method,action", [("patch", "join"), ("delete", "leave")])
def test_legacy_team_route_refuses_before_touching_the_team(load, monkeypatch, method, action):
    views, client = load("api.messages.messages_views")
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    seen = []
    monkeypatch.setattr(rules, "check_team_membership_change",
                        lambda u, t, a, admin=False: seen.append((t, a, admin)) or rules.refusal(rules.TEAM_INACTIVE))
    service = "join_team" if action == "join" else "unjoin_team"
    monkeypatch.setattr(views, service, lambda *a: pytest.fail("service must not run on a refusal"))

    res = getattr(client, method)("/api/messages/team", json={"teamId": "t1"}, headers=HEADERS)

    assert res.status_code == 409
    assert res.get_json()["error"] == "team_inactive"
    assert seen == [("t1", action, False)]


@pytest.mark.parametrize("method,action", [("patch", "join"), ("delete", "leave")])
def test_legacy_team_route_runs_service_when_allowed(load, monkeypatch, method, action):
    views, client = load("api.messages.messages_views")
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: True)
    seen = []
    monkeypatch.setattr(rules, "check_team_membership_change",
                        lambda u, t, a, admin=False: seen.append(admin) or None)
    from api.messages.message import Message
    service = "join_team" if action == "join" else "unjoin_team"
    monkeypatch.setattr(views, service, lambda u, body: Message("Joined Team"))

    res = getattr(client, method)("/api/messages/team", json={"teamId": "t1"}, headers=HEADERS)

    assert res.status_code == 200
    assert res.get_json()["text"] == "Joined Team"
    assert seen == [True]


def test_queue_route_refuses_unapproved_without_side_effects(load, monkeypatch):
    views, client = load("api.teams.teams_views")
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    monkeypatch.setattr(rules, "caller_is_approved_hacker", lambda u, e: False)
    monkeypatch.setattr(views, "queue_team", lambda *a: pytest.fail("queue_team must not run"))

    res = client.post("/api/team/queue", json={"eventId": "fall-2026", "name": "x"}, headers=HEADERS)

    assert res.status_code == 403
    body = res.get_json()
    assert body["error"] == "not_approved_hacker" and body["message"]


def test_queue_route_allows_approved_hacker(load, monkeypatch):
    views, client = load("api.teams.teams_views")
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)
    monkeypatch.setattr(rules, "caller_is_approved_hacker", lambda u, e: True)
    monkeypatch.setattr(views, "queue_team", lambda u, body: {"success": True})

    res = client.post("/api/team/queue", json={"eventId": "fall-2026"}, headers=HEADERS)

    assert res.status_code == 200 and res.get_json()["success"] is True
