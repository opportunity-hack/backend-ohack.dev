"""
POST /api/team/admin/<teamid>/message (send_team_message).

Regression: every call 500'd with `'LoggedInUser' object has no attribute
'first_name'` (PropelAuth's flask user carries no names), and the email loop
imported a module that doesn't exist. Now: Slack and/or email, per-channel
results, history only when something was delivered.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import api.teams.teams_service as svc
import services.volunteers_service as volunteers_service
from api.teams.tests._fake_firestore import FakeDB


class LoggedInUser:
    """Mirrors propelauth_flask's user: user_id + email, NO first/last name."""

    def __init__(self):
        self.user_id = "propel-admin"
        self.email = "admin@example.org"


@pytest.fixture
def env(monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_WEBHOOK", "SLACK_USER_TOKEN"):
        monkeypatch.setenv(var, "")
    db = FakeDB()
    ada = db.put("users", "u1", {"name": "Ada", "email_address": "ada@example.org"})
    bob = db.put("users", "u2", {"name": "Bob", "email_address": ""})
    cy = db.put("users", "u3", {"name": "Cy", "email_address": "ADA@example.org"})  # duplicate address
    db.put("teams", "team-1", {"name": "Rockets", "slack_channel": "rockets", "users": [ada, bob, cy]})
    db.put("teams", "team-noslack", {"name": "Quiet", "users": [ada]})

    slack_posts, emails = [], []
    monkeypatch.setattr(svc, "get_db", lambda: db)
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kw: None)
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "send_slack", lambda message, channel: slack_posts.append((channel, message)))

    def fake_email(**kw):
        emails.append(kw)
        return {"success": True}

    monkeypatch.setattr(volunteers_service, "send_email_to_address", fake_email)
    return db, slack_posts, emails


def test_slack_only_default_no_longer_crashes(env):
    db, slack_posts, emails = env
    res = svc.send_team_message(LoggedInUser(), "team-1", {"message": "Hello team"})
    assert res["success"] is True
    assert slack_posts == [("rockets", "Hello team")]
    assert emails == []
    history = db.doc("teams", "team-1")["communication_history"]
    assert history[-1]["sender"] == "admin@example.org"
    assert history[-1]["channels"] == ["slack"]


def test_email_goes_to_every_member_with_an_address_once(env):
    db, slack_posts, emails = env
    res = svc.send_team_message(
        LoggedInUser(), "team-1",
        {"message": "Demo at 3", "channels": {"slack": False, "email": True}, "subject": "Demo time"},
    )
    assert res["success"] is True
    assert slack_posts == []
    assert [e["email"] for e in emails] == ["ada@example.org"]
    assert emails[0]["subject"] == "Demo time"
    assert res["delivery"]["email"]["missing_email"] == ["Bob"]
    assert db.doc("teams", "team-1")["communication_history"][-1]["channels"] == ["email"]


def test_both_channels(env):
    _, slack_posts, emails = env
    res = svc.send_team_message(
        LoggedInUser(), "team-1", {"message": "Hi", "channels": {"slack": True, "email": True}}
    )
    assert res["success"] is True
    assert len(slack_posts) == 1 and len(emails) == 1
    assert emails[0]["subject"] == "Update for Rockets"


def test_slack_failure_is_reported_not_a_500(env, monkeypatch):
    db, _, emails = env

    def boom(message, channel):
        raise RuntimeError("channel_not_found")

    monkeypatch.setattr(svc, "send_slack", boom)
    body, status = svc.send_team_message(LoggedInUser(), "team-1", {"message": "Hi"})
    assert status == 502
    assert body["error"] == "delivery_failed"
    assert "channel_not_found" in body["delivery"]["slack"]["error"]
    assert "communication_history" not in db.doc("teams", "team-1")

    # Email still goes out when Slack fails
    res = svc.send_team_message(LoggedInUser(), "team-1", {"message": "Hi", "channels": {"slack": True, "email": True}})
    assert res["success"] is True
    assert res["delivery"]["slack"]["sent"] is False
    assert len(emails) == 1


def test_team_without_slack_channel(env):
    body, status = svc.send_team_message(LoggedInUser(), "team-noslack", {"message": "Hi"})
    assert status == 502
    assert body["delivery"]["slack"]["error"] == "This team has no Slack channel."


@pytest.mark.parametrize("payload,code", [
    ({"message": "  "}, "empty_message"),
    ({"message": "Hi", "channels": {"slack": False, "email": False}}, "no_channel"),
])
def test_bad_requests(env, payload, code):
    body, status = svc.send_team_message(LoggedInUser(), "team-1", payload)
    assert status == 400 and body["error"] == code


def test_unknown_team(env):
    body, status = svc.send_team_message(LoggedInUser(), "nope", {"message": "Hi"})
    assert status == 404
