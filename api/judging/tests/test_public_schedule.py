"""
Public judging schedule (Nov 2026) — GET /api/judge/schedule/<event_id>.

Unauthenticated, so the contract under test is mostly about what must NOT
leave the service: nothing before the publish switch, and no judge user_id /
email / training links ever. The db layer is patched like the sibling tests.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import pytest

import api.judging.judging_service as svc
from model.judge_panel import JudgePanel

EVENT = "fall-2026"


def _panel(doc_id, name, room):
    p = JudgePanel()
    p.id, p.panel_name, p.room, p.event_id = doc_id, name, room, EVENT
    return p


def _a(judge_id, team_id, round_name="round1", panel_id="pA", room=None, demo_time=None):
    return {"id": f"{judge_id}-{team_id}-{round_name}", "judge_id": judge_id, "team_id": team_id,
            "round": round_name, "panel_id": panel_id, "room": room, "demo_time": demo_time,
            "event_id": EVENT}


JUDGES = [
    {"id": "vol-ada", "user_id": "prop-ada", "name": "Ada Lovelace", "title": "CTO",
     "companyName": "Engines Inc", "photoUrl": "https://cdn.ohack.dev/ada.png",
     "linkedinProfile": "https://linkedin.com/in/ada", "email": "ada@example.org",
     "judgeTrainingIntroCertUrl": "https://lms.ohack.dev/certificate/x",
     "introductionVideoUrl": "https://youtu.be/x", "isSelected": True},
    {"id": "vol-bob", "user_id": "prop-bob", "name": "Bob Builder", "company": "Bobco",
     "email": "bob@example.org", "isSelected": True},
]

TEAMS = [
    {"id": "t1", "name": "Zeta", "slack_channel": "team-1", "admin_notes": "secret"},
    {"id": "t2", "name": "alpha", "slack_channel": "team-2"},
    {"id": "t3", "name": "Gamma", "slack_channel": "team-3"},
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    svc.clear_public_schedule_cache()
    yield
    svc.clear_public_schedule_cache()


@pytest.fixture
def db(monkeypatch):
    state = {
        "event": {"event_id": EVENT, "title": "Fall 2026", "timezone": "America/Phoenix",
                  "location": "ASU Tempe", "constraints": {"judging_schedule_published": True}},
        "assignments": [],
        "panels": [_panel("pA", "Panel A", "Room 101"), _panel("pB", "Panel 10", "Room 210"),
                   _panel("pC", "Panel 9", "Room 209"), _panel("pR2", "Round 2 - Final Judging", "Main hall")],
        "calls": {"assignments": 0},
    }

    def _assignments(event_id):
        state["calls"]["assignments"] += 1
        return {"data": list(state["assignments"])}

    monkeypatch.setattr(svc, "get_single_hackathon_event", lambda e: dict(state["event"]) if state["event"] else {})
    monkeypatch.setattr(svc, "fetch_judge_assignments_by_event_id", _assignments)
    monkeypatch.setattr(svc, "fetch_judge_panels_by_event", lambda e: list(state["panels"]))
    monkeypatch.setattr(svc, "get_volunteer_from_db_by_event", lambda e, t, admin=False: {"data": [dict(j) for j in JUDGES]})
    monkeypatch.setattr(svc, "get_teams_batch", lambda body: [dict(t) for t in TEAMS if t["id"] in body["team_ids"]])
    monkeypatch.setattr(svc, "get_round_locks", lambda e, public=False: {
        "round1": {"event_id": e, "round": "round1", "locked": False},
        "round2": {"event_id": e, "round": "round2", "locked": False}})
    return state


def test_unknown_event_is_not_found(db):
    db["event"] = {}
    assert svc.get_public_judging_schedule("nope") == {"error": "event_not_found"}


def test_unpublished_returns_no_panels_and_no_judges(db):
    db["event"]["constraints"] = {}
    db["assignments"] = [_a("prop-ada", "t1")]
    result = svc.get_public_judging_schedule(EVENT)
    assert result["published"] is False
    assert result["rounds"] == {"round1": [], "round2": []}
    assert "Ada" not in str(result)
    assert db["calls"]["assignments"] == 0  # nothing read before publish
    assert result["event"]["title"] == "Fall 2026"
    assert set(result["locks"]) == {"round1", "round2"}


def test_published_groups_by_round_and_panel_and_emits_public_fields_only(db):
    db["assignments"] = [
        _a("prop-ada", "t1"), _a("prop-bob", "t1"), _a("prop-ada", "t2"), _a("prop-bob", "t2"),
        _a("prop-ada", "t1"),  # duplicate doc from the old per-pair POSTs
        _a("prop-bob", "t3", panel_id="pB"),
    ]
    result = svc.get_public_judging_schedule(EVENT)
    r1 = result["rounds"]["round1"]
    assert [p["panel_name"] for p in r1] == ["Panel 10", "Panel A"]  # natural sort: digits first
    panel_a = r1[1]
    assert panel_a["panel_id"] == "pA" and panel_a["room"] == "Room 101"
    # judges deduped + sorted by name, public fields only
    assert [j["name"] for j in panel_a["judges"]] == ["Ada Lovelace", "Bob Builder"]
    ada = panel_a["judges"][0]
    assert set(ada) == set(svc.PUBLIC_JUDGE_FIELDS)
    assert ada == {"id": "vol-ada", "name": "Ada Lovelace", "title": "CTO", "company": "Engines Inc",
                   "photoUrl": "https://cdn.ohack.dev/ada.png", "linkedinProfile": "https://linkedin.com/in/ada"}
    assert panel_a["judges"][1]["company"] == "Bobco"
    blob = str(result)
    for leaked in ("prop-ada", "prop-bob", "ada@example.org", "lms.ohack.dev", "youtu.be", "secret"):
        assert leaked not in blob
    # teams deduped + sorted by name (case-insensitive), room inherited from the panel
    assert [t["name"] for t in panel_a["teams"]] == ["alpha", "Zeta"]
    assert panel_a["teams"][0] == {"id": "t2", "name": "alpha", "slack_channel": "team-2",
                                   "demo_time": None, "room": "Room 101"}
    assert result["rounds"]["round2"] == []


def test_panels_sort_naturally(db):
    db["assignments"] = [_a("prop-ada", "t1", panel_id="pB"), _a("prop-ada", "t2", panel_id="pC")]
    names = [p["panel_name"] for p in svc.get_public_judging_schedule(EVENT)["rounds"]["round1"]]
    assert names == ["Panel 9", "Panel 10"]


def test_unknown_panel_falls_back_to_assignment_room_and_counts_missing_judges(db):
    db["assignments"] = [_a("prop-ada", "t1", panel_id="gone", room="Lab 3"),
                         _a("prop-ghost", "t1", panel_id="gone", room="Lab 3")]
    result = svc.get_public_judging_schedule(EVENT)
    panel = result["rounds"]["round1"][0]
    assert panel["panel_name"] == svc.UNKNOWN_PANEL_NAME and panel["room"] == "Lab 3"
    assert [j["name"] for j in panel["judges"]] == ["Ada Lovelace"]
    assert result["unassigned_judge_ids_count"] == 1


def test_round2_teams_sorted_by_demo_time_then_name(db):
    db["assignments"] = [
        _a("prop-ada", "t1", "round2", "pR2", demo_time="2025-01-01T15:20:00"),
        _a("prop-ada", "t2", "round2", "pR2", demo_time=None),
        _a("prop-ada", "t3", "round2", "pR2", demo_time="2025-01-01T15:00:00"),
    ]
    panel = svc.get_public_judging_schedule(EVENT)["rounds"]["round2"][0]
    assert panel["panel_name"] == "Round 2 - Final Judging" and panel["room"] == "Main hall"
    assert [t["id"] for t in panel["teams"]] == ["t3", "t1", "t2"]


def test_deleted_team_docs_are_skipped(db):
    db["assignments"] = [_a("prop-ada", "t1"), _a("prop-ada", "deleted-team")]
    panel = svc.get_public_judging_schedule(EVENT)["rounds"]["round1"][0]
    assert [t["id"] for t in panel["teams"]] == ["t1"]


def test_read_failure_is_an_error_and_not_cached(db):
    db["assignments"] = [_a("prop-ada", "t1")]
    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value={"data": [], "error": "boom"}):
        assert svc.get_public_judging_schedule(EVENT) == {"error": "read_failed"}
    assert svc.get_public_judging_schedule(EVENT)["rounds"]["round1"]  # recovered, not cached


def test_cache_is_cleared_by_bulk_assign(db, monkeypatch):
    db["assignments"] = [_a("prop-ada", "t1")]
    first = svc.get_public_judging_schedule(EVENT)
    db["assignments"].append(_a("prop-ada", "t2"))
    assert svc.get_public_judging_schedule(EVENT) == first  # cached
    monkeypatch.setattr(svc, "insert_judge_assignment", lambda a: a)
    out = svc.bulk_create_judge_assignments(EVENT, "round1", "pA", ["prop-ada"], ["t1", "t2"])
    assert out["success"] is True
    teams = svc.get_public_judging_schedule(EVENT)["rounds"]["round1"][0]["teams"]
    assert [t["id"] for t in teams] == ["t2", "t1"]  # alpha, Zeta


def test_cache_is_cleared_by_panel_edits(db, monkeypatch):
    db["assignments"] = [_a("prop-ada", "t1")]
    assert svc.get_public_judging_schedule(EVENT)["rounds"]["round1"][0]["room"] == "Room 101"
    db["panels"][0].room = "Room 999"
    monkeypatch.setattr(svc, "fetch_judge_panel", lambda pid: db["panels"][0])
    monkeypatch.setattr(svc, "update_judge_panel", lambda p: p)
    assert svc.update_judge_panel_details("pA", room="Room 999")["success"] is True
    assert svc.get_public_judging_schedule(EVENT)["rounds"]["round1"][0]["room"] == "Room 999"


def test_hackathon_clear_cache_also_clears_the_schedule(db, monkeypatch):
    db["event"]["constraints"] = {}
    assert svc.get_public_judging_schedule(EVENT)["published"] is False
    db["event"]["constraints"] = {"judging_schedule_published": True}
    import services.hackathons_service as hs
    monkeypatch.setattr(hs, "clear_all_caches", lambda: None)
    monkeypatch.setattr("common.utils.redis_cache.delete_cached", lambda key: None)
    hs.clear_cache()
    assert svc.get_public_judging_schedule(EVENT)["published"] is True


# ---------------------------------------------------------------------------
# View: public route, status codes
# ---------------------------------------------------------------------------

def test_schedule_route_is_public_and_maps_errors(monkeypatch):
    import api.judging.judging_views as views
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    client = app.test_client()

    monkeypatch.setattr(views, "get_public_judging_schedule", lambda e: {"event_id": e, "published": False,
                                                                         "rounds": {"round1": [], "round2": []}})
    res = client.get(f"/api/judge/schedule/{EVENT}")  # no Authorization header
    assert res.status_code == 200 and res.get_json()["published"] is False

    monkeypatch.setattr(views, "get_public_judging_schedule", lambda e: {"error": "event_not_found"})
    assert client.get("/api/judge/schedule/nope").status_code == 404

    monkeypatch.setattr(views, "get_public_judging_schedule", lambda e: {"error": "read_failed"})
    assert client.get(f"/api/judge/schedule/{EVENT}").status_code == 500


# ---------------------------------------------------------------------------
# Judge dashboard: Round 1 teams now carry room + panel name
# ---------------------------------------------------------------------------

class _A:
    def __init__(self, team_id, round_name, panel_id=None, room=None, demo_time=None):
        self.team_id, self.round, self.panel_id, self.room, self.demo_time = team_id, round_name, panel_id, room, demo_time


def test_get_judge_teams_round1_carries_room_and_panel_name(monkeypatch):
    monkeypatch.setattr(svc, "_batch_users_for_teams", lambda teams: {})
    monkeypatch.setattr(svc, "get_round_locks", lambda e, public=False: {})
    assignments = [_A("t1", "round1", panel_id="pA"), _A("t2", "round1", panel_id="pA", room="Lab 4"),
                   _A("t1", "round2", panel_id="pR2", room="Main", demo_time="2025-01-01T15:00:00")]
    with patch.object(svc, "fetch_judge_assignments_by_event_and_judge", return_value=assignments), \
            patch.object(svc, "fetch_judge_panels_by_event",
                         return_value=[_panel("pA", "Panel A", "Room 101"), _panel("pR2", "Finals", None)]), \
            patch.object(svc, "get_teams_batch", return_value=[{"id": "t1", "name": "A", "users": []},
                                                                {"id": "t2", "name": "B", "users": []}]), \
            patch.object(svc, "fetch_judge_scores_by_judge_and_event", return_value=[]), \
            patch.object(svc, "get_nonprofit_by_id", return_value={"name": "NP"}), \
            patch.object(svc, "get_single_hackathon_event", return_value={"title": "Fall 2026"}):
        result = svc.get_judge_teams("judge-1", EVENT)

    r1 = {t["id"]: t for t in result["round1_teams"]}
    assert r1["t1"]["room"] == "Room 101" and r1["t1"]["panel_name"] == "Panel A"
    assert r1["t2"]["room"] == "Lab 4"  # the assignment's own room wins
    assert r1["t1"]["demo_time"] is None
    r2 = result["round2_teams"][0]
    assert r2["room"] == "Main" and r2["panel_name"] == "Finals" and r2["demo_time"] == "2025-01-01T15:00:00"


def test_get_judge_teams_survives_a_panel_read_failure(monkeypatch):
    monkeypatch.setattr(svc, "_batch_users_for_teams", lambda teams: {})
    monkeypatch.setattr(svc, "get_round_locks", lambda e, public=False: {})

    def boom(_e):
        raise RuntimeError("panels down")
    with patch.object(svc, "fetch_judge_assignments_by_event_and_judge", return_value=[_A("t1", "round1", "pA")]), \
            patch.object(svc, "fetch_judge_panels_by_event", side_effect=boom), \
            patch.object(svc, "get_teams_batch", return_value=[{"id": "t1", "name": "A", "users": []}]), \
            patch.object(svc, "fetch_judge_scores_by_judge_and_event", return_value=[]), \
            patch.object(svc, "get_nonprofit_by_id", return_value={}), \
            patch.object(svc, "get_single_hackathon_event", return_value={}):
        result = svc.get_judge_teams("judge-1", EVENT)
    assert result["round1_teams"][0]["room"] is None
