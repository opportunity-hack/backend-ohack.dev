"""
POST /api/team/approve (approve_team): the admin approves a team for an
exact project (problem statement). Covers: problemStatementId required for a
multi-project nonprofit / auto-filled for a single-project one; the team gets
problem_statements: [ref]; the repo README seed and the repo → project link
use THAT project; the "already exists" repo path; the double-approve claim
(409); refusals are 4xx instead of the old TypeError / KeyError /
UnboundLocalError / IndexError crashes. Also /me's new `project` and
edit_team's problem_statement_id.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from datetime import datetime, timedelta

import pytest

import api.teams.gateway_keys as gateway_keys
import api.teams.teams_service as svc
from api.teams.tests._fake_firestore import FakeDB, FakeTransaction


def _npo_json(db, npo_id):
    """What get_single_npo returns (doc_to_json → problem statement ids)."""
    data = db.doc("nonprofits", npo_id)
    if data is None:
        return {"nonprofits": None}
    out = dict(data)
    out["id"] = npo_id
    out["problem_statements"] = [ref.id for ref in data.get("problem_statements") or []]
    return {"nonprofits": out}


@pytest.fixture
def env(monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_WEBHOOK", "SLACK_USER_TOKEN"):
        monkeypatch.setenv(var, "")
    db = FakeDB()
    db.put("problem_statements", "ps-a1", {"title": "Volunteer scheduler", "github": []})
    db.put("problem_statements", "ps-a2", {"title": "Donor CRM"})
    db.put("problem_statements", "ps-c1", {"title": "Food map"})
    db.put("nonprofits", "npo-a", {"name": "Alpha", "problem_statements": [db.ref("problem_statements", "ps-a1"), db.ref("problem_statements", "ps-a2")]})
    db.put("nonprofits", "npo-c", {"name": "Gamma", "problem_statements": [db.ref("problem_statements", "ps-c1")]})
    db.put("nonprofits", "npo-empty", {"name": "Empty", "problem_statements": []})
    member = db.put("users", "u1", {"name": "Ada", "user_id": "oauth2|slack|T1-U1"})
    db.put("teams", "team-1", {
        "name": "Rockets", "slack_channel": "rockets", "github_username": "ada",
        "hackathon_event_id": "fall-2026", "status": "IN_REVIEW", "users": [member],
        # The team's own pre-approval guess must lose to the admin's pick.
        "problem_statements": ["ps-a2"],
    })
    hackathon = {"id": "hack-doc", "event_id": "fall-2026", "github_org": "Opportunity-Hack-2026", "links": []}

    calls = {"slack": [], "repos": [], "seed_team_data": [], "keys": []}
    monkeypatch.setattr(svc, "get_db", lambda: db)
    monkeypatch.setattr(svc, "_in_transaction", lambda _db, body: body(FakeTransaction()))
    monkeypatch.setattr(svc, "get_single_npo", lambda npo_id: _npo_json(db, npo_id))
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda event_id: hackathon if event_id == "fall-2026" else None)
    monkeypatch.setattr(svc, "get_propel_user_details_by_id", lambda pid: (None, None, None, None, "Admin Person", None))
    monkeypatch.setattr(svc, "send_slack", lambda msg, channel: calls["slack"].append((channel, msg)))
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "get_teams_list", lambda team_id: {"id": team_id})
    monkeypatch.setattr(gateway_keys, "provision_team_gateway_key", lambda team_id: calls["keys"].append(team_id) or {})

    def fake_repo(repository_name, *args, **kwargs):
        calls["repos"].append(repository_name)
        return {"repo_name": repository_name, "full_url": f"https://github.com/Opportunity-Hack-2026/{repository_name}"}

    monkeypatch.setattr(svc, "create_github_repo", fake_repo)

    real_seed = svc._build_repo_seed_files

    def spy_seed(_db, team_data, *args, **kwargs):
        calls["seed_team_data"].append(dict(team_data))
        return real_seed(_db, team_data, *args, **kwargs)

    monkeypatch.setattr(svc, "_build_repo_seed_files", spy_seed)
    return db, calls


def approve(**body):
    return svc.approve_team("admin-propel", body)


def _team(db):
    return db.doc("teams", "team-1")


def _refused(result, code, status=400):
    assert isinstance(result, tuple), result
    payload, got = result
    assert got == status, payload
    assert payload["error"] == code
    assert payload["message"]
    return payload


def test_multi_project_nonprofit_requires_a_project(env):
    db, calls = env
    _refused(approve(teamId="team-1", nonprofitId="npo-a"), "problem_statement_required")
    assert _team(db)["status"] == "IN_REVIEW"
    assert calls["repos"] == [] and calls["slack"] == []


def test_project_must_belong_to_the_nonprofit(env):
    db, calls = env
    _refused(approve(teamId="team-1", nonprofitId="npo-a", problemStatementId="ps-c1"), "invalid_problem_statement")
    assert calls["repos"] == []


def test_single_project_nonprofit_auto_fills(env):
    db, calls = env
    result = approve(teamId="team-1", nonprofitId="npo-c")
    assert result["success"] is True
    assert result["problem_statement_id"] == "ps-c1"
    assert [ref.id for ref in _team(db)["problem_statements"]] == ["ps-c1"]


def test_approval_writes_project_and_links_seed_to_it(env):
    db, calls = env
    result = approve(teamId="team-1", nonprofitId="npo-a", problemStatementId="ps-a1")

    assert result["success"] is True
    team = _team(db)
    assert team["status"] == "NONPROFIT_SELECTED"
    assert team["selected_nonprofit_id"] == "npo-a"
    assert [ref.id for ref in team["problem_statements"]] == ["ps-a1"]
    assert team["github_links"][0]["link"].endswith(calls["repos"][0])

    # README seed got the chosen project, not the team's earlier ps-a2
    (seen,) = calls["seed_team_data"]
    assert seen["problem_statements"] == ["ps-a1"]
    ps_id, ps = svc._select_problem_statement(db, seen, {})
    assert ps_id == "ps-a1" and ps["title"] == "Volunteer scheduler"

    # The repo shows on the chosen project's page, and only there
    assert db.doc("problem_statements", "ps-a1")["github"][0]["link"] == team["github_links"][0]["link"]
    assert "github" not in db.doc("problem_statements", "ps-a2")

    channel_msg = next(msg for ch, msg in calls["slack"] if ch == "rockets")
    assert "*Project:* <https://www.ohack.dev/project/ps-a1|Volunteer scheduler>" in channel_msg
    assert calls["keys"] == ["team-1"]


def test_double_approve_is_409(env):
    db, calls = env
    assert approve(teamId="team-1", nonprofitId="npo-c")["success"] is True
    _refused(approve(teamId="team-1", nonprofitId="npo-c"), "already_approved", 409)
    assert len(calls["repos"]) == 1
    assert len([1 for ch, _ in calls["slack"] if ch == "rockets"]) == 1


def test_concurrent_approver_loses_the_claim(env, monkeypatch):
    """Both admins pass the plain read; only the transaction decides."""
    db, calls = env
    real_claim = svc._claim_team_for_approval

    def other_admin_got_there_first(_db, team_ref, admin):
        team_ref.update({"status": svc.APPROVING_STATUS, "approval_started_at": datetime.utcnow().isoformat()})
        return real_claim(_db, team_ref, admin)

    monkeypatch.setattr(svc, "_claim_team_for_approval", other_admin_got_there_first)
    _refused(approve(teamId="team-1", nonprofitId="npo-c"), "already_approved", 409)
    assert calls["repos"] == [] and calls["slack"] == []


def test_stale_claim_is_taken_over(env):
    """A worker that died mid-approval leaves APPROVING behind; after
    APPROVAL_CLAIM_STALE_AFTER another admin can approve."""
    db, calls = env
    stale = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    db.ref("teams", "team-1").update({"status": svc.APPROVING_STATUS, "approval_started_at": stale})
    result = approve(teamId="team-1", nonprofitId="npo-c")
    assert result["success"] is True
    assert _team(db)["status"] == "NONPROFIT_SELECTED"
    assert _team(db)["approval_started_by"] == "admin-propel"


def test_fresh_claim_blocks_a_second_approver(env):
    db, calls = env
    db.ref("teams", "team-1").update({"status": svc.APPROVING_STATUS, "approval_started_at": datetime.utcnow().isoformat()})
    _refused(approve(teamId="team-1", nonprofitId="npo-c"), "already_approved", 409)
    assert calls["repos"] == []


def test_repo_failure_returns_team_to_review(env, monkeypatch):
    db, calls = env

    def boom(*args, **kwargs):
        raise ValueError("Repository creation failed: rate limited")

    monkeypatch.setattr(svc, "create_github_repo", boom)
    payload = _refused(approve(teamId="team-1", nonprofitId="npo-c"), "github_repo_failed", 502)
    assert "rate limited" in payload["message"]
    assert _team(db)["status"] == "IN_REVIEW"
    assert calls["slack"] == []
    # and it can be approved again once GitHub is back
    monkeypatch.setattr(svc, "create_github_repo", lambda name, *a, **k: {"repo_name": name, "full_url": f"https://github.com/o/{name}"})
    assert approve(teamId="team-1", nonprofitId="npo-c")["success"] is True


def test_unexpected_crash_returns_team_to_review(env, monkeypatch):
    db, calls = env

    def crash(*args, **kwargs):
        raise RuntimeError("slack down")

    monkeypatch.setattr(svc, "send_slack", crash)
    with pytest.raises(RuntimeError):
        approve(teamId="team-1", nonprofitId="npo-c")
    assert _team(db)["status"] == "IN_REVIEW"


def test_repo_already_exists_uses_the_existing_repo(env, monkeypatch):
    """The ValueError("... already exists") branch used to leave `repo`
    unassigned → UnboundLocalError."""
    db, calls = env

    def exists(*args, **kwargs):
        raise ValueError("name already exists on this account")

    monkeypatch.setattr(svc, "create_github_repo", exists)
    result = approve(teamId="team-1", nonprofitId="npo-c")
    assert result["success"] is True
    link = _team(db)["github_links"][0]["link"]
    assert link.startswith("https://github.com/Opportunity-Hack-2026/Rockets-")
    assert db.doc("problem_statements", "ps-c1")["github"][0]["link"] == link


def test_unknown_nonprofit_is_a_400(env):
    _refused(approve(teamId="team-1", nonprofitId="npo-nope"), "nonprofit_not_found")


def test_nonprofit_without_projects_is_a_400(env):
    _refused(approve(teamId="team-1", nonprofitId="npo-empty"), "no_problem_statements")


@pytest.mark.parametrize("body", [{}, {"teamId": "team-1"}, {"nonprofitId": "npo-c"}])
def test_missing_keys_are_a_400(env, body):
    _refused(svc.approve_team("admin-propel", body), "missing_fields")


def test_none_body_is_a_400(env):
    _refused(svc.approve_team("admin-propel", None), "missing_fields")


def test_unknown_team_is_a_404(env):
    _refused(approve(teamId="team-nope", nonprofitId="npo-c"), "team_not_found", 404)


def test_team_with_no_members_is_a_400(env):
    db, calls = env
    db.ref("teams", "team-1").update({"users": []})
    _refused(approve(teamId="team-1", nonprofitId="npo-c"), "team_has_no_members")
    assert _team(db)["status"] == "IN_REVIEW"


def test_inactive_team_is_not_in_review(env):
    db, calls = env
    db.ref("teams", "team-1").update({"status": "INACTIVE"})
    _refused(approve(teamId="team-1", nonprofitId="npo-c"), "not_in_review", 409)


# ---------------------------------------------------------------------------
# edit_team problem_statement_id (admin moves an approved team)
# ---------------------------------------------------------------------------

def test_edit_team_moves_team_to_another_project(env):
    db, _ = env
    db.ref("teams", "team-1").update({"status": "NONPROFIT_SELECTED", "selected_nonprofit_id": "npo-a"})
    result = svc.edit_team({"id": "team-1", "problem_statement_id": "ps-a2"})
    assert result["success"] is True
    assert [ref.id for ref in _team(db)["problem_statements"]] == ["ps-a2"]


def test_edit_team_refuses_a_project_of_another_nonprofit(env):
    db, _ = env
    db.ref("teams", "team-1").update({"selected_nonprofit_id": "npo-a"})
    payload, status = svc.edit_team({"id": "team-1", "problem_statement_id": "ps-c1"})
    assert status == 400 and payload["error"] == "invalid_problem_statement"
    assert _team(db)["problem_statements"] == ["ps-a2"]  # untouched


def test_edit_team_uses_the_nonprofit_in_the_same_request(env):
    db, _ = env
    db.ref("teams", "team-1").update({"selected_nonprofit_id": "npo-a"})
    result = svc.edit_team({"id": "team-1", "selected_nonprofit_id": "npo-c", "problem_statement_id": "ps-c1"})
    assert result["success"] is True
    team = _team(db)
    assert team["selected_nonprofit_id"] == "npo-c"
    assert [ref.id for ref in team["problem_statements"]] == ["ps-c1"]


def test_edit_team_clears_the_project(env):
    db, _ = env
    assert svc.edit_team({"id": "team-1", "problem_statement_id": None})["success"] is True
    assert _team(db)["problem_statements"] == []


# ---------------------------------------------------------------------------
# GET /api/team/<event_id>/me → project: {id, title}
# ---------------------------------------------------------------------------

def test_my_teams_carry_the_project_and_stay_private(env, monkeypatch):
    db, _ = env
    db.ref("teams", "team-1").update({
        "problem_statements": [db.ref("problem_statements", "ps-a1")],
        "nonprofit_rankings": [{"nonprofit_id": "npo-a", "problem_statement_id": "ps-a1", "rank": 1}],
        "admin_notes": "secret",
    })
    db.put("teams", "team-2", {"name": "Other", "users": [db.ref("users", "u1")]})  # no project yet
    hackathon = {"id": "hack-doc", "teams": [db.ref("teams", "team-1"), db.ref("teams", "team-2")]}
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda event_id: hackathon)
    monkeypatch.setattr(svc, "get_propel_user_details_by_id",
                        lambda pid: (None, "oauth2|slack|T1-U1", None, None, "Ada", None))

    teams = {t["id"]: t for t in svc.get_my_teams_by_event_id("propel-ada", "fall-2026")["teams"]}

    assert teams["team-1"]["project"] == {"id": "ps-a1", "title": "Volunteer scheduler"}
    assert teams["team-2"]["project"] is None
    for team in teams.values():
        assert "problem_statements" not in team and "users" not in team
        assert "nonprofit_rankings" not in team and "admin_notes" not in team
