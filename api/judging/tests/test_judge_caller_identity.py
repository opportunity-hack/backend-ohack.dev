"""
Judge caller identity (Class B, reliability plan Nov 2026).

The judge pages send the caller's PropelAuth id; admins assign judges by the
id stored on the judge's volunteer doc. These tests run EVERY judge-facing
route against the four ways a judge doc is found in practice and assert the
route hands the STORED id to the service (which is what assignments, scores
and drafts are keyed by) — the judging process itself is untouched.

The real shared resolver (services.volunteers_service._find_volunteer_snapshot)
runs against a tiny in-memory Firestore; only the PropelAuth lookup is stubbed.
"""
import functools
import importlib
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

pytestmark = pytest.mark.real_judge_identity

VIEWS_MODULE = "api.judging.judging_views"
CALLER = "6f1c2d3e-0000-4000-8000-00000000c411"   # PropelAuth UUID of the logged-in judge
CALLER_EMAIL = "judge@example.com"
CALLER_OAUTH = "oauth2|slack|T1-U1"
EVENT = "fall-2026"
TEAM = "team-1"

# name -> (volunteer docs in Firestore, the stored id assignments are keyed by)
IDENTITY_CASES = {
    "propel_uuid": (
        [("vol-1", {"user_id": CALLER, "email": "x@example.com"})],
        CALLER,
    ),
    "email_only_blank_user_id": (     # Sheets import: no user_id, admin assigns by doc id
        [("vol-doc-7", {"user_id": "", "email": CALLER_EMAIL})],
        "vol-doc-7",
    ),
    "email_other_login": (            # applied under another login with the same email
        [("vol-2", {"user_id": "11111111-2222-4333-8444-555555555555", "email": CALLER_EMAIL})],
        "11111111-2222-4333-8444-555555555555",
    ),
    "legacy_oauth": (
        [("vol-3", {"user_id": CALLER_OAUTH, "email": "old@example.com"})],
        CALLER_OAUTH,
    ),
}


# --------------------------------------------------------------------------
# In-memory Firestore just big enough for the resolver's equality queries.
# --------------------------------------------------------------------------
class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _Query:
    def __init__(self, docs, filters=(), limit=None):
        self._docs, self._filters, self._limit = docs, list(filters), limit

    def where(self, field, op, value):
        assert op == "=="
        return _Query(self._docs, self._filters + [(field, value)], self._limit)

    def limit(self, n):
        return _Query(self._docs, self._filters, n)

    def stream(self):
        out = [_Snap(i, d) for i, d in self._docs
               if all(d.get(f) == v for f, v in self._filters)]
        return iter(out[: self._limit] if self._limit else out)


class _FakeDb:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        assert name == "volunteers"
        return _Query(self._docs)


def _judge_docs(docs):
    return [(i, {"event_id": EVENT, "volunteer_type": "judge", **d}) for i, d in docs]


# --------------------------------------------------------------------------
# Views with auth stubbed (same pattern as test_judging_views_locks.py).
# --------------------------------------------------------------------------
def _passthrough_decorator_factory(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = types.SimpleNamespace(user_id=CALLER, email=CALLER_EMAIL)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@pytest.fixture
def views(monkeypatch):
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
def client(views):
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    return app.test_client()


def _install_identity(monkeypatch, docs):
    import db.db as db_module
    import services.volunteers_service as vs
    fake = _FakeDb(_judge_docs(docs))
    monkeypatch.setattr(db_module, "get_db", lambda: fake)
    monkeypatch.setattr(vs, "_caller_identities",
                        lambda pid: (pid, CALLER_EMAIL, CALLER_OAUTH))


def _install_services(views, monkeypatch, stored_id):
    """Service doubles that only 'know' the stored judge id, so a route that
    passed the login id instead would 403/come back empty."""
    calls = {}

    def rec(name, result):
        def fn(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result(*args, **kwargs) if callable(result) else result
        return fn

    monkeypatch.setattr(views, "is_round_locked", lambda e, r: False)
    monkeypatch.setattr(views, "is_judge_assigned_for_round",
                        rec("assigned_round", lambda jid, e, t, r: jid == stored_id))
    monkeypatch.setattr(views, "is_judge_assigned_to_team",
                        rec("assigned_team", lambda jid, t: jid == stored_id))
    monkeypatch.setattr(views, "get_judge_teams", rec("teams", lambda jid, e: {"judge": jid, "round1_teams": []}))
    monkeypatch.setattr(views, "get_judge_scores", rec("scores", lambda jid, e: {"judge": jid, "scores": []}))
    monkeypatch.setattr(views, "submit_judge_score", rec("submit", {"success": True}))
    monkeypatch.setattr(views, "save_draft_score", rec("draft", {"success": True}))
    monkeypatch.setattr(views, "get_draft_score", rec("get_draft", lambda jid, t, e, r: {"judge": jid}))
    monkeypatch.setattr(views, "get_individual_judge_score",
                        rec("individual", lambda jid, t, e, r, d: {"judge": jid}))
    monkeypatch.setattr(views, "get_team_details", rec("team_details", {"id": TEAM}))
    monkeypatch.setattr(views, "get_judge_event_details",
                        rec("event_details", lambda jid, e: {"judge": {"id": jid}}))
    monkeypatch.setattr(views, "get_judge_assignments", rec(
        "assignments",
        lambda jid: {"hackathons": [{"event_id": EVENT, "round1_teams": 3, "round2_teams": 0}]}
        if jid == stored_id else {"hackathons": []}))
    return calls


SCORE_BODY = {"judge_id": CALLER, "team_id": TEAM, "event_id": EVENT, "round": "round1",
              "scores": {"scopeImpact": 3}, "feedback": {"general": "x" * 30}}


@pytest.mark.parametrize("case", list(IDENTITY_CASES))
def test_every_judge_route_uses_the_stored_judge_id(case, views, client, monkeypatch):
    docs, stored_id = IDENTITY_CASES[case]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)

    res = client.get(f"/api/judge/teams/{CALLER}/{EVENT}")
    assert res.status_code == 200 and res.get_json()["judge"] == stored_id

    res = client.get(f"/api/judge/scores/{CALLER}/{EVENT}")
    assert res.status_code == 200 and res.get_json()["judge"] == stored_id

    res = client.post("/api/judge/score", json=SCORE_BODY)
    assert res.status_code == 200
    assert calls["submit"][-1][1]["judge_id"] == stored_id

    res = client.post("/api/judge/draft", json=SCORE_BODY)
    assert res.status_code == 200
    assert calls["draft"][-1][1]["judge_id"] == stored_id

    res = client.get(f"/api/judge/draft/{CALLER}/{TEAM}/{EVENT}/round1")
    assert res.status_code == 200 and res.get_json()["judge"] == stored_id

    res = client.get(f"/api/judge/score/{CALLER}/{TEAM}/{EVENT}/round1")
    assert res.status_code == 200 and res.get_json()["judge"] == stored_id

    res = client.get(f"/api/judge/team/{TEAM}")
    assert res.status_code == 200

    res = client.get(f"/api/judge/assignments/{CALLER}")
    assert res.status_code == 200
    assert [h["event_id"] for h in res.get_json()["hackathons"]] == [EVENT]

    res = client.get(f"/api/judge/judge/{CALLER}/event/{EVENT}")
    assert res.status_code == 200 and res.get_json()["judge"]["id"] == stored_id


def test_propel_keyed_judge_makes_no_extra_service_calls(views, client, monkeypatch):
    """The common case is unchanged: one assignments call with the login id."""
    docs, stored_id = IDENTITY_CASES["propel_uuid"]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)

    client.get(f"/api/judge/assignments/{CALLER}")
    assert [c[0][0] for c in calls["assignments"]] == [CALLER]
    client.get(f"/api/judge/team/{TEAM}")
    assert [c[0][0] for c in calls["assigned_team"]] == [CALLER]


def test_a_judge_can_also_name_themselves_by_stored_id(views, client, monkeypatch):
    docs, stored_id = IDENTITY_CASES["legacy_oauth"]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)
    res = client.post("/api/judge/score", json={**SCORE_BODY, "judge_id": stored_id})
    assert res.status_code == 200
    assert calls["submit"][-1][1]["judge_id"] == stored_id


@pytest.mark.parametrize("case", list(IDENTITY_CASES))
def test_another_judges_id_is_still_forbidden(case, views, client, monkeypatch):
    docs, stored_id = IDENTITY_CASES[case]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)
    other = "99999999-0000-4000-8000-000000000000"

    assert client.post("/api/judge/score", json={**SCORE_BODY, "judge_id": other}).status_code == 403
    assert client.post("/api/judge/draft", json={**SCORE_BODY, "judge_id": other}).status_code == 403
    assert client.get(f"/api/judge/teams/{other}/{EVENT}").status_code == 403
    assert client.get(f"/api/judge/scores/{other}/{EVENT}").status_code == 403
    assert client.get(f"/api/judge/draft/{other}/{TEAM}/{EVENT}/round1").status_code == 403
    assert client.get(f"/api/judge/score/{other}/{TEAM}/{EVENT}/round1").status_code == 403
    assert client.get(f"/api/judge/assignments/{other}").status_code == 403
    assert "submit" not in calls and "draft" not in calls


def test_round_assignment_and_lock_checks_still_apply(views, client, monkeypatch):
    docs, stored_id = IDENTITY_CASES["email_only_blank_user_id"]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)

    # Not assigned for round2 -> 403, even with a resolved identity.
    monkeypatch.setattr(views, "is_judge_assigned_for_round",
                        lambda jid, e, t, r: jid == stored_id and r == "round1")
    assert client.post("/api/judge/score", json={**SCORE_BODY, "round": "round2"}).status_code == 403

    # Locked round -> 409, nothing written.
    monkeypatch.setattr(views, "is_round_locked", lambda e, r: True)
    res = client.post("/api/judge/score", json=SCORE_BODY)
    assert res.status_code == 409 and res.get_json()["error"] == "round_locked"
    assert "submit" not in calls


def test_admin_lookup_of_a_stored_judge_id_is_unchanged(views, client, monkeypatch):
    """Admin pages call /judge/<stored id>/event/<e> for OTHER judges."""
    docs, stored_id = IDENTITY_CASES["legacy_oauth"]
    _install_identity(monkeypatch, docs)
    calls = _install_services(views, monkeypatch, stored_id)
    res = client.get(f"/api/judge/judge/oauth2|slack|T9-U9/event/{EVENT}")
    assert res.status_code == 200
    assert calls["event_details"][-1][0][0] == "oauth2|slack|T9-U9"


def test_no_judge_doc_falls_back_to_the_login_id(views, client, monkeypatch):
    _install_identity(monkeypatch, [])
    calls = _install_services(views, monkeypatch, CALLER)
    assert client.post("/api/judge/score", json=SCORE_BODY).status_code == 200
    assert calls["submit"][-1][1]["judge_id"] == CALLER


def test_resolver_failure_falls_back_to_the_login_id(views, client, monkeypatch):
    import services.volunteers_service as vs
    _install_identity(monkeypatch, [])
    monkeypatch.setattr(vs, "_find_volunteer_snapshot", MagicMock(side_effect=RuntimeError("firestore down")))
    calls = _install_services(views, monkeypatch, CALLER)
    res = client.get(f"/api/judge/teams/{CALLER}/{EVENT}")
    assert res.status_code == 200 and res.get_json()["judge"] == CALLER
    assert calls["teams"][-1][0][0] == CALLER


def test_assignments_merge_across_events_keeps_one_entry_per_event(views):
    merged = views._merge_assignment_lists([
        {"hackathons": [{"event_id": "a", "round1_teams": 1, "round2_teams": 0}]},
        {"hackathons": [{"event_id": "a", "round1_teams": 4, "round2_teams": 1},
                        {"event_id": "b", "round1_teams": 2, "round2_teams": 0}]},
    ])
    assert [(h["event_id"], h["round1_teams"]) for h in merged["hackathons"]] == [("a", 4), ("b", 2)]
