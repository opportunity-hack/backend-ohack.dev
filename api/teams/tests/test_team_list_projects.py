"""GET /api/team/<hackathon_id> (get_teams_by_hackathon_id) carries each
team's assigned project as id strings.

It used to pop `problem_statements` (refs aren't JSON-serializable), so the
admin edit dialog opened an approved team with no assigned project, preselected
a default one and offered "Save project" — on a nonprofit with several
projects, one click moved the team off the project it was approved for.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import api.teams.teams_service as svc
from api.teams.tests._fake_firestore import FakeDB


def test_list_returns_project_ids_not_refs(monkeypatch):
    db = FakeDB()
    ps_ref = db.put("problem_statements", "ps-2", {"title": "Volunteer app"})
    user_ref = db.put("users", "u1", {"name": "Ada"})
    approved = db.put("teams", "t1", {
        "name": "Approved", "status": "NONPROFIT_SELECTED",
        "users": [user_ref], "problem_statements": [ps_ref],
    })
    legacy = db.put("teams", "t2", {"name": "Legacy", "users": []})
    mixed = db.put("teams", "t3", {"name": "Mixed", "users": [],
                                   "problem_statements": ["ps-9", None, ""]})
    db.put("hackathons", "h1", {"teams": [approved, legacy, mixed]})
    monkeypatch.setattr(svc, "get_db", lambda: db)

    teams = {t["id"]: t for t in svc.get_teams_by_hackathon_id("h1")["teams"]}

    assert teams["t1"]["problem_statements"] == ["ps-2"]
    assert teams["t2"]["problem_statements"] == []
    assert teams["t3"]["problem_statements"] == ["ps-9"]
    assert "users" not in teams["t1"]
    assert [m["name"] for m in teams["t1"]["team_members"]] == ["Ada"]
