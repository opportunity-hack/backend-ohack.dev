"""
POST /api/team/queue (queue_team): teams rank 2–5 individual projects
(problem statements), validated against get_team_project_options, and every
refusal happens BEFORE the Slack channel is created (it used to create the
channel first and then refuse, orphaning channels) as a real 400
{error, message} (the invalid-GitHub case used to answer 200).
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import time

import pytest

import api.teams.teams_service as svc
from api.teams.tests._fake_firestore import FakeDB
from api.teams.tests.test_project_options import seed_event


@pytest.fixture
def env(monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_WEBHOOK", "SLACK_USER_TOKEN"):
        monkeypatch.setenv(var, "")
    db = FakeDB()
    svc._PROJECT_OPTIONS_CACHE.clear()
    hackathon = seed_event(db)
    db.put("hackathons", "hack-doc", {"event_id": "fall-2026"})
    creator = db.put("users", "creator-doc", {"user_id": "oauth2|slack|T1-UCREATOR", "name": "Ada", "teams": []})

    calls = {"slack_channels": [], "slack_messages": [], "invites": []}
    monkeypatch.setattr(svc, "get_db", lambda: db)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id",
                        lambda event_id: hackathon if event_id == "fall-2026" else None)
    monkeypatch.setattr(svc, "validate_github_username", lambda name: name != "bad@example.com")
    monkeypatch.setattr(svc, "get_propel_user_details_by_id",
                        lambda pid: (None, "oauth2|slack|T1-UCREATOR", None, None, "Ada", None))
    state = {"creator": creator}
    monkeypatch.setattr(svc, "get_user_doc_reference", lambda uid: state["creator"])
    monkeypatch.setattr(svc, "create_slack_channel", lambda name: calls["slack_channels"].append(name) or "C123")
    monkeypatch.setattr(svc, "add_bot_to_channel", lambda cid: None)
    monkeypatch.setattr(svc, "send_slack", lambda msg, channel: calls["slack_messages"].append((channel, msg)))
    monkeypatch.setattr(svc, "invite_user_to_channel", lambda uid, channel: calls["invites"].append(uid))
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "get_teams_list", lambda team_id: {"id": team_id})
    monkeypatch.setattr(time, "sleep", lambda s: None)
    yield db, calls, state
    svc._PROJECT_OPTIONS_CACHE.clear()


def body(rankings, **overrides):
    payload = {
        "name": "Rockets",
        "slackChannel": "rockets-fall26",
        "eventId": "fall-2026",
        "githubUsername": "ada",
        "nonprofitRankings": rankings,
        "teamMembers": [],
    }
    payload.update(overrides)
    return payload


def r(npo, ps, rank):
    return {"nonprofit_id": npo, "problem_statement_id": ps, "rank": rank}


def _teams(db):
    return {k[1]: v for k, v in db.store.items() if k[0] == "teams"}


def _assert_refused(result, code, db, calls):
    assert isinstance(result, tuple), result
    payload, status = result
    assert status == 400
    assert payload["error"] == code
    assert payload["message"]
    # No side effects: validation runs before any Slack call or team write.
    assert calls["slack_channels"] == []
    assert calls["slack_messages"] == []
    assert calls["invites"] == []
    assert _teams(db) == {}


def test_valid_rankings_are_normalized_and_stored(env):
    db, calls, _ = env
    # Two projects of the same nonprofit plus one of another; ranks out of
    # order and gappy — stored in rank order as 1..n.
    result = svc.queue_team("propel-1", body([r("npo-c", "ps-c1", 9), r("npo-a", "ps-a2", 2), r("npo-a", "ps-a1", 1)]))

    assert result["success"] is True
    (team,) = _teams(db).values()
    assert team["nonprofit_rankings"] == [
        r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2), r("npo-c", "ps-c1", 3),
    ]
    assert team["status"] == "IN_REVIEW"
    assert calls["slack_channels"] == ["rockets-fall26"]
    channel_msg = next(msg for ch, msg in calls["slack_messages"] if ch == "rockets-fall26")
    assert "#1 Volunteer scheduler — Alpha" in channel_msg
    assert "#3 Food map — Gamma" in channel_msg


def test_too_few_projects(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1)]))
    _assert_refused(result, "invalid_rankings", db, calls)
    assert "at least 2" in result[0]["message"]


def test_too_many_projects(env, monkeypatch):
    db, calls, _ = env
    options = {"nonprofits": [{"id": "npo-x", "name": "X", "projects": [{"id": f"p{i}", "title": f"P{i}"} for i in range(7)]}]}
    monkeypatch.setattr(svc, "get_team_project_options", lambda event_id: options)
    result = svc.queue_team("propel-1", body([r("npo-x", f"p{i}", i + 1) for i in range(6)]))
    _assert_refused(result, "invalid_rankings", db, calls)
    assert "at most 5" in result[0]["message"]


def test_fewer_available_than_minimum_requires_all(env, monkeypatch):
    db, calls, _ = env
    options = {"nonprofits": [{"id": "npo-x", "name": "X", "projects": [{"id": "only", "title": "Only"}]}]}
    monkeypatch.setattr(svc, "get_team_project_options", lambda event_id: options)
    result = svc.queue_team("propel-1", body([r("npo-x", "only", 1)]))
    assert result["success"] is True


def test_project_from_the_wrong_nonprofit(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-c", "ps-a1", 1), r("npo-a", "ps-a2", 2)]))
    _assert_refused(result, "invalid_rankings", db, calls)


def test_hidden_project_is_refused(env, monkeypatch):
    db, calls, _ = env
    hidden = seed_event(db, visible=["ps-a1", "ps-c1"])
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda event_id: hidden)
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2)]))
    _assert_refused(result, "invalid_rankings", db, calls)


def test_production_project_is_refused(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-b", "ps-b1", 2)]))
    _assert_refused(result, "invalid_rankings", db, calls)


def test_duplicates_are_dropped_then_counted(env):
    db, calls, _ = env
    # The same project twice is ONE ranking → below the minimum of 2.
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a1", 2)]))
    _assert_refused(result, "invalid_rankings", db, calls)

    ok = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a1", 2), r("npo-c", "ps-c1", 3)]))
    assert ok["success"] is True
    (team,) = _teams(db).values()
    assert team["nonprofit_rankings"] == [r("npo-a", "ps-a1", 1), r("npo-c", "ps-c1", 2)]


def test_mixed_legacy_and_project_entries_refused(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), {"nonprofit_id": "npo-c", "rank": 2}]))
    _assert_refused(result, "invalid_rankings", db, calls)


def test_legacy_nonprofit_only_rankings_still_accepted(env):
    """The pre-release frontend sends [{nonprofit_id, rank}] — keep team
    creation working between the backend and frontend deploys."""
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([{"nonprofit_id": "npo-c", "rank": 2}, {"nonprofit_id": "npo-a", "rank": 1}]))
    assert result["success"] is True
    (team,) = _teams(db).values()
    assert team["nonprofit_rankings"] == [{"nonprofit_id": "npo-a", "rank": 1}, {"nonprofit_id": "npo-c", "rank": 2}]


def test_legacy_rankings_with_a_foreign_nonprofit_refused(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([{"nonprofit_id": "npo-elsewhere", "rank": 1}]))
    _assert_refused(result, "invalid_rankings", db, calls)


def test_invalid_github_username_is_a_400_before_slack(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2)], githubUsername="bad@example.com"))
    _assert_refused(result, "invalid_github_username", db, calls)


@pytest.mark.parametrize("missing", ["name", "slackChannel", "eventId", "githubUsername"])
def test_missing_fields(env, missing):
    db, calls, _ = env
    payload = body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2)])
    payload.pop(missing)
    _assert_refused(svc.queue_team("propel-1", payload), "missing_fields", db, calls)


def test_unknown_event(env):
    db, calls, _ = env
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2)], eventId="nope"))
    _assert_refused(result, "event_not_found", db, calls)


def test_missing_creator_profile_refused_before_side_effects(env):
    """get_user_doc_reference → None used to crash AFTER the team was
    inserted and the Slack channel created."""
    db, calls, state = env
    state["creator"] = None
    result = svc.queue_team("propel-1", body([r("npo-a", "ps-a1", 1), r("npo-a", "ps-a2", 2)]))
    _assert_refused(result, "user_not_found", db, calls)


def test_none_body_is_missing_fields(env):
    db, calls, _ = env
    _assert_refused(svc.queue_team("propel-1", None), "missing_fields", db, calls)
