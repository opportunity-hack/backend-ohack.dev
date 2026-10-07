"""
Team docs carry staff-only internals (admin_notes, nonprofit_rankings,
comments, communication_history). Every public/member-facing getter must strip
them via services.teams_service.public_team_view; mentor_* fields stay public
by design. Admins read the full doc through GET /api/team/admin/<teamid>, and
GET /api/team/<hackathon_id> keeps the full payload only for admins.
"""
import functools
import importlib
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import flask
import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

import services.hackathons_service as hs
import services.teams_service as ts

PRIVATE = ("admin_notes", "nonprofit_rankings", "comments", "communication_history")


def _full_team(team_id="team-1"):
    return {
        "id": team_id,
        "name": "Rockets",
        "admin_notes": "flaky team",
        "nonprofit_rankings": ["npo-a"],
        "comments": "we want npo-a",
        "communication_history": [{"text": "hi"}],
        "mentor_ratings": [{"criterion": "scope", "rating": 3}],
        "mentor_notes": [{"text": "good"}],
        "users": [],
    }


def test_public_team_view_strips_private_keys_and_is_none_safe():
    team = _full_team()
    view = ts.public_team_view(team)
    for key in PRIVATE:
        assert key not in view
    assert view["mentor_ratings"] == team["mentor_ratings"]
    assert "admin_notes" in team  # input untouched
    assert ts.public_team_view(None) is None


def _db_with_team(team):
    doc = MagicMock()
    doc.exists = True
    doc.id = team["id"]
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = doc
    db.collection.return_value.where.return_value.stream.return_value = [doc]
    db.collection.return_value.stream.return_value = [doc]
    return db


def test_get_team_omits_private_fields_but_keeps_mentor_data(monkeypatch):
    team = _full_team()
    monkeypatch.setattr(ts, "get_db", lambda: _db_with_team(team))
    monkeypatch.setattr(ts, "doc_to_json", lambda docid, doc: dict(team))
    monkeypatch.setattr(ts, "_enrich_team_users", lambda data, db: data)

    payload = ts.get_team("team-1")["team"]

    for key in PRIVATE:
        assert key not in payload
    assert payload["mentor_ratings"] and payload["mentor_notes"]


def test_get_teams_list_and_batch_omit_private_fields(monkeypatch):
    team = _full_team()
    monkeypatch.setattr(ts, "get_db", lambda: _db_with_team(team))
    monkeypatch.setattr(ts, "doc_to_json", lambda docid, doc: dict(team))

    single = ts.get_teams_list("team-1")
    [listed] = ts.get_teams_list()["teams"]
    [batched] = ts.get_teams_batch({"team_ids": ["team-1"]})

    for payload in (single, listed, batched):
        for key in PRIVATE:
            assert key not in payload
        assert payload["mentor_ratings"]


def test_get_team_admin_returns_full_doc(monkeypatch):
    team = _full_team()
    monkeypatch.setattr(ts, "get_db", lambda: _db_with_team(team))
    monkeypatch.setattr(ts, "doc_to_json", lambda docid, doc: dict(team))
    monkeypatch.setattr(ts, "_enrich_team_users", lambda data, db: data)

    assert ts.get_team_admin("team-1")["admin_notes"] == "flaky team"


def test_single_hackathon_event_teams_omit_private_fields(monkeypatch):
    hs.get_single_hackathon_event.cache_clear()
    team_ref = types.SimpleNamespace(id="team-1")
    monkeypatch.setattr(
        hs,
        "get_hackathon_by_event_id",
        lambda event_id: {"id": "doc-1", "event_id": "event-1", "nonprofits": [], "teams": [team_ref]},
    )
    monkeypatch.setattr(hs, "doc_to_json", lambda doc=None, docid=None: _full_team(docid))
    monkeypatch.setattr(hs, "_enrich_teams_users_batch", lambda teams, db: teams)
    monkeypatch.setattr(hs, "_get_db", lambda: MagicMock())

    [team] = hs.get_single_hackathon_event("event-1")["teams"]

    for key in PRIVATE:
        assert key not in team
    assert team["mentor_ratings"]
    hs.get_single_hackathon_event.cache_clear()


# ---- route level -----------------------------------------------------------

VIEWS_MODULE = "api.teams.teams_views"
FAKE_USER = types.SimpleNamespace(user_id="propel-uuid", email="u@example.com")


def _passthrough(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _rejecting_require_user(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not flask.request.headers.get("Authorization"):
            return {"error": "unauthorized"}, 401
        g.propelauth_current_user = FAKE_USER
        return fn(*args, **kwargs)

    return wrapper


def _full_list_payload():
    team = _full_team()
    team.pop("users")
    team["team_members"] = [{
        "id": "user-doc-1",
        "user_id": "oauth2|slack|T1-U1",
        "name": "Ada",
        "nickname": "ada",
        "profile_image": "https://img/ada.png",
        "email_address": "ada@example.com",
        "phone_number": "555",
    }]
    return {"teams": [team]}


@pytest.fixture
def client(monkeypatch):
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough,
        require_user=_rejecting_require_user,
        optional_user=_passthrough(),
    )
    stub.auth_user = LocalProxy(lambda: g.get("propelauth_current_user"))
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    views = importlib.import_module(VIEWS_MODULE)
    monkeypatch.setattr(views, "get_teams_by_hackathon_id", lambda hackathon_id: _full_list_payload())
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: False)

    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client()
    sys.modules.pop(VIEWS_MODULE, None)


AUTH = {"Authorization": "Bearer x"}


def test_non_admin_team_list_is_public_view_with_slim_members(client):
    response = client.get("/api/team/hack-1", headers=AUTH)
    assert response.status_code == 200
    [team] = response.get_json()["teams"]
    for key in PRIVATE:
        assert key not in team
    assert team["mentor_ratings"]
    [member] = team["team_members"]
    assert set(member) == {"id", "user_id", "name", "nickname", "profile_image"}


def test_admin_team_list_keeps_full_payload(client, monkeypatch):
    monkeypatch.setattr("services.hackathon_planning_service.is_admin", lambda user: True)
    [team] = client.get("/api/team/hack-1", headers=AUTH).get_json()["teams"]
    assert team["admin_notes"] == "flaky team"
    assert team["team_members"][0]["email_address"] == "ada@example.com"


def test_admin_team_detail_route_returns_full_doc(client, monkeypatch):
    monkeypatch.setattr("services.teams_service.get_team_admin", lambda team_id: _full_team(team_id))
    response = client.get("/api/team/admin/team-1", headers=AUTH)
    assert response.status_code == 200
    assert response.get_json()["team"]["admin_notes"] == "flaky team"


def test_admin_team_detail_route_404s_missing_team(client, monkeypatch):
    monkeypatch.setattr("services.teams_service.get_team_admin", lambda team_id: None)
    response = client.get("/api/team/admin/nope", headers=AUTH)
    assert response.status_code == 404
    assert response.get_json() == {"error": "not_found"}


def test_admin_team_detail_route_requires_login(client):
    assert client.get("/api/team/admin/team-1").status_code == 401


def test_my_teams_omit_private_fields(monkeypatch):
    import api.teams.teams_service as api_ts

    user_ref = types.SimpleNamespace(id="user-doc-1")
    team_doc = MagicMock()
    team_doc.exists = True
    team_doc.id = "team-1"
    team_doc.to_dict.return_value = {**_full_team(), "users": [user_ref]}
    user_doc = MagicMock()
    user_doc.exists = True
    user_doc.id = "user-doc-1"
    user_doc.to_dict.return_value = {"user_id": "oauth2|slack|T1-U1"}
    db = MagicMock()
    db.get_all.side_effect = [[team_doc], [user_doc]]

    monkeypatch.setattr(api_ts, "get_propel_user_details_by_id", lambda pid: (None, "oauth2|slack|T1-U1", None, None, "Ada", None))
    monkeypatch.setattr(api_ts, "get_hackathon_by_event_id", lambda eid: {"teams": [object()]})
    monkeypatch.setattr(api_ts, "get_db", lambda: db)

    [team] = api_ts.get_my_teams_by_event_id("propel-uuid", "event-1")["teams"]
    for key in PRIVATE:
        assert key not in team
    assert team["mentor_ratings"]
