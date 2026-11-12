"""GET /api/users/profile/teams — the own-profile "Your teams" payload.

Service-level: the identity resolver and the two data reads are patched so
no Firestore/redis is touched (ENVIRONMENT=test -> MockFirestore anyway).
"""
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import db.db as dbmod
import services.users_service as us
from model.user import User


def _user(db_id="u-db-1"):
    u = User()
    u.id = db_id
    u.name = "Test User"
    u.user_id = "oauth2|slack|T1-U1"
    return u


EVENTS = {
    "fall-2026": {"event_id": "fall-2026", "title": "Fall 2026", "start_date": "2026-10-10",
                  "end_date": "2026-10-12", "location": "Tempe, AZ", "image_url": "x.png",
                  "constraints": {"secret": True}},
    "2025_fall": {"event_id": "2025_fall", "title": "Fall 2025", "start_date": "2025-10-11",
                  "end_date": "2025-10-13"},
}


def _patch(monkeypatch, user, teams):
    monkeypatch.setattr(us, "_resolve_and_ensure_user", lambda _pid: (user, user.user_id if user else None))
    monkeypatch.setattr(dbmod, "fetch_user_portfolio_teams", lambda db_id: list(teams) if db_id == "u-db-1" else [])
    # common.utils.firebase needs FIREBASE_CERT_CONFIG at import; the service
    # imports it lazily, so a stub module is enough here.
    fb = types.ModuleType("common.utils.firebase")
    fb.get_hackathon_by_event_id = lambda eid: EVENTS.get(eid)
    monkeypatch.setitem(sys.modules, "common.utils.firebase", fb)


def test_get_my_teams_attaches_trimmed_event_and_sorts_newest_first(monkeypatch):
    teams = [
        {"id": "t-old", "name": "Old Owls", "hackathon_event_id": "2025_fall", "status": "COMPLETED_HACKATHON"},
        {"id": "t-new", "name": "Night Owls", "hackathon_event_id": "fall-2026", "status": "NONPROFIT_SELECTED",
         "slack_channel": "npo-night-owls"},
        {"id": "t-none", "name": "No event"},
    ]
    _patch(monkeypatch, _user(), teams)

    payload = us.get_my_teams("propel-1")

    assert [t["id"] for t in payload["teams"]] == ["t-new", "t-old", "t-none"]
    new = payload["teams"][0]
    assert new["event"] == {
        "event_id": "fall-2026", "title": "Fall 2026", "start_date": "2026-10-10",
        "end_date": "2026-10-12", "location": "Tempe, AZ", "image_url": "x.png",
    }
    assert "constraints" not in new["event"]  # trimmed, nothing extra leaks
    assert payload["teams"][2]["event"] is None


def test_get_my_teams_empty_when_user_has_no_teams(monkeypatch):
    _patch(monkeypatch, _user(), [])
    assert us.get_my_teams("propel-1") == {"teams": []}


def test_get_my_teams_none_when_identity_unresolved(monkeypatch):
    _patch(monkeypatch, None, [])
    assert us.get_my_teams("propel-x") is None


def test_get_my_teams_swallows_read_failures(monkeypatch):
    _patch(monkeypatch, _user(), [])

    def boom(_db_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(dbmod, "fetch_user_portfolio_teams", boom)
    assert us.get_my_teams("propel-1") == {"teams": []}
