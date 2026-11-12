"""
GET /api/team/<event_id>/project-options (get_team_project_options): the ONE
rule for which projects a team can rank at creation. queue_team validates
against the same function, so these cases are the contract for both.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import api.teams.teams_service as svc
from api.teams.tests._fake_firestore import FakeDB


def seed_event(db, visible=None, extra_hackathon=None):
    """Event with three nonprofits:
    - npo-a: two projects (ps-a1, ps-a2)
    - npo-b: one production project only (ps-b1) → nothing selectable
    - npo-c: one project (ps-c1), long description, skills as a string
    """
    db.put("problem_statements", "ps-a1", {"title": "Volunteer scheduler", "description": "Shifts.", "skills": ["react"], "status": "hackathon"})
    db.put("problem_statements", "ps-a2", {"title": "Donor CRM", "description": "Donors.", "skills": [], "status": "concept"})
    db.put("problem_statements", "ps-b1", {"title": "Live app", "description": "Done.", "status": "production"})
    db.put("problem_statements", "ps-c1", {"title": "Food map", "description": "x" * 900, "skills": "python, maps"})
    db.put("nonprofits", "npo-a", {"name": "Alpha", "image": "a.png", "website": "https://a.org",
                                   "problem_statements": [db.ref("problem_statements", "ps-a1"), db.ref("problem_statements", "ps-a2")]})
    db.put("nonprofits", "npo-b", {"name": "Beta", "problem_statements": [db.ref("problem_statements", "ps-b1")]})
    db.put("nonprofits", "npo-c", {"name": "Gamma", "problem_statements": [db.ref("problem_statements", "ps-c1")]})
    hackathon = {
        "id": "hack-doc",
        "event_id": "fall-2026",
        "nonprofits": [db.ref("nonprofits", "npo-a"), db.ref("nonprofits", "npo-b"), db.ref("nonprofits", "npo-c")],
    }
    if visible is not None:
        hackathon["visible_problem_statements"] = visible
    hackathon.update(extra_hackathon or {})
    return hackathon


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()
    svc._PROJECT_OPTIONS_CACHE.clear()
    state = {"hackathon": seed_event(db)}
    monkeypatch.setattr(svc, "get_db", lambda: db)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id",
                        lambda event_id: state["hackathon"] if event_id == "fall-2026" else None)
    yield db, state
    svc._PROJECT_OPTIONS_CACHE.clear()


def _ids(options):
    return {npo["id"]: [p["id"] for p in npo["projects"]] for npo in options["nonprofits"]}


def test_lists_selectable_projects_grouped_by_nonprofit(wired):
    options = svc.get_team_project_options("fall-2026")

    assert options["event_id"] == "fall-2026"
    assert _ids(options) == {"npo-a": ["ps-a1", "ps-a2"], "npo-c": ["ps-c1"]}
    alpha = options["nonprofits"][0]
    assert alpha["name"] == "Alpha" and alpha["image"] == "a.png" and alpha["website"] == "https://a.org"
    assert alpha["projects"][0] == {
        "id": "ps-a1", "title": "Volunteer scheduler", "description": "Shifts.",
        "skills": ["react"], "status": "hackathon",
    }


def test_production_projects_dropped_and_empty_nonprofits_dropped(wired):
    options = svc.get_team_project_options("fall-2026")
    assert "npo-b" not in _ids(options)


def test_description_trimmed_and_skills_string_split(wired):
    options = svc.get_team_project_options("fall-2026")
    food = options["nonprofits"][1]["projects"][0]
    assert len(food["description"]) <= svc.PROJECT_DESCRIPTION_PREVIEW_CHARS + 1
    assert food["description"].endswith("…")
    assert food["skills"] == ["python", "maps"]


def test_visible_problem_statements_is_an_allowlist(wired):
    db, state = wired
    state["hackathon"] = seed_event(db, visible=["ps-a2"])
    options = svc.get_team_project_options("fall-2026")
    assert _ids(options) == {"npo-a": ["ps-a2"]}


def test_empty_visible_list_hides_everything(wired):
    db, state = wired
    state["hackathon"] = seed_event(db, visible=[])
    assert svc.get_team_project_options("fall-2026")["nonprofits"] == []


def test_string_refs_and_missing_docs_tolerated(wired):
    db, state = wired
    state["hackathon"] = {"id": "h", "event_id": "fall-2026", "nonprofits": ["npo-c", "npo-missing", None]}
    assert _ids(svc.get_team_project_options("fall-2026")) == {"npo-c": ["ps-c1"]}


def test_unknown_event_returns_none(wired):
    assert svc.get_team_project_options("nope") is None
    assert svc.get_team_project_options("") is None


def test_result_is_cached_and_callers_cannot_mutate_the_cache(wired):
    db, state = wired
    first = svc.get_team_project_options("fall-2026")
    first["nonprofits"].clear()
    state["hackathon"] = {"id": "h", "event_id": "fall-2026", "nonprofits": []}
    again = svc.get_team_project_options("fall-2026")
    assert _ids(again) == {"npo-a": ["ps-a1", "ps-a2"], "npo-c": ["ps-c1"]}


# ---------------------------------------------------------------------------
# Route wiring (propelauth stubbed — same pattern as
# test_teams_devpost_demo_video_views.py)
# ---------------------------------------------------------------------------

import functools
import importlib
import sys
import types

from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.teams.teams_views"
FAKE_USER = types.SimpleNamespace(user_id="caller-propel-uuid", email="caller@example.com")


def _passthrough(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@pytest.fixture
def client(monkeypatch):
    stub = types.ModuleType("common.auth")
    stub.auth = types.SimpleNamespace(
        require_org_member_with_permission=_passthrough,
        require_user=_passthrough(),
        optional_user=_passthrough(),
    )
    stub.auth_user = LocalProxy(lambda: g.propelauth_current_user)
    monkeypatch.setitem(sys.modules, "common.auth", stub)
    sys.modules.pop(VIEWS_MODULE, None)
    views = importlib.import_module(VIEWS_MODULE)
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    yield app.test_client(), views
    sys.modules.pop(VIEWS_MODULE, None)


def test_route_returns_options(client, monkeypatch):
    test_client, views = client
    payload = {"event_id": "fall-2026", "nonprofits": [{"id": "npo-a", "projects": [{"id": "ps-a1"}]}]}
    monkeypatch.setattr(views, "get_team_project_options", lambda event_id: payload if event_id == "fall-2026" else None)

    res = test_client.get("/api/team/fall-2026/project-options")
    assert res.status_code == 200
    assert res.get_json() == payload


def test_route_404s_unknown_event(client, monkeypatch):
    test_client, views = client
    monkeypatch.setattr(views, "get_team_project_options", lambda event_id: None)

    res = test_client.get("/api/team/nope/project-options")
    assert res.status_code == 404
    assert res.get_json()["error"] == "event_not_found"


def test_route_does_not_shadow_other_team_routes(client):
    _, views = client
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    adapter = app.url_map.bind("localhost")
    assert adapter.match("/api/team/fall-2026/project-options", method="GET")[0].endswith("get_team_project_options_api")
    assert adapter.match("/api/team/fall-2026/me", method="GET")[0].endswith("get_my_teams_by_event_if_api")
    assert adapter.match("/api/team/team-1/gateway-key", method="GET")[0].endswith("get_gateway_key_api")
    assert adapter.match("/api/team/admin/gateway-keys", method="GET")[0].endswith("list_gateway_key_statuses_api")
