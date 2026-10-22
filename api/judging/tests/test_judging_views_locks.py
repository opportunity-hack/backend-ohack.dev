"""
Route-level tests: a locked round rejects score + draft writes with 409
`round_locked`, the lock/progress/bulk admin routes validate input, and every
view signature matches its URL params (copied from the submissions views
test — propelauth decorators don't catch that at import time).
"""
import functools
import importlib
import inspect
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.judging.judging_views"
FAKE_USER = types.SimpleNamespace(user_id="judge-1", email="judge@example.com")


def _passthrough_decorator_factory(*_args, **_kwargs):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            g.propelauth_current_user = FAKE_USER
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


HEADERS = {"Authorization": "Bearer test", "X-Org-Id": "org"}
SCORE_BODY = {
    "judge_id": "judge-1", "team_id": "team-1", "event_id": "fall-2026", "round": "round1",
    "scores": {"scopeImpact": 3}, "feedback": {"general": "x" * 30},
}


def test_every_view_signature_matches_its_url_params(views):
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    mismatches = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = inspect.unwrap(app.view_functions[rule.endpoint])
        declared = {name for name, p in inspect.signature(view).parameters.items()
                    if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        if declared != set(rule.arguments):
            mismatches.append((rule.rule, sorted(declared), sorted(rule.arguments)))
    assert mismatches == []


def test_locked_round_rejects_score_submit_with_409(views, client, monkeypatch):
    submit = MagicMock(return_value={"success": True})
    monkeypatch.setattr(views, "is_judge_assigned_to_team", lambda *_: True)
    monkeypatch.setattr(views, "is_round_locked", lambda e, r: True)
    monkeypatch.setattr(views, "submit_judge_score", submit)

    res = client.post("/api/judge/score", json=SCORE_BODY, headers=HEADERS)
    assert res.status_code == 409
    assert res.get_json()["error"] == "round_locked"
    assert res.get_json()["round"] == "round1"
    submit.assert_not_called()


def test_unlocked_round_submits_score(views, client, monkeypatch):
    submit = MagicMock(return_value={"success": True, "score_id": "s1"})
    monkeypatch.setattr(views, "is_judge_assigned_to_team", lambda *_: True)
    monkeypatch.setattr(views, "is_round_locked", lambda e, r: False)
    monkeypatch.setattr(views, "submit_judge_score", submit)

    res = client.post("/api/judge/score", json=SCORE_BODY, headers=HEADERS)
    assert res.status_code == 200
    submit.assert_called_once()


def test_locked_round_rejects_draft_save_too(views, client, monkeypatch):
    save = MagicMock(return_value={"success": True})
    monkeypatch.setattr(views, "is_judge_assigned_to_team", lambda *_: True)
    monkeypatch.setattr(views, "is_round_locked", lambda e, r: True)
    monkeypatch.setattr(views, "save_draft_score", save)

    res = client.post("/api/judge/draft", json=SCORE_BODY, headers=HEADERS)
    assert res.status_code == 409
    save.assert_not_called()


def test_unknown_round_is_400_before_lock_check(views, client, monkeypatch):
    monkeypatch.setattr(views, "is_judge_assigned_to_team", lambda *_: True)
    called = MagicMock(return_value=False)
    monkeypatch.setattr(views, "is_round_locked", called)
    res = client.post("/api/judge/score", json={**SCORE_BODY, "round": "finals"}, headers=HEADERS)
    assert res.status_code == 400
    called.assert_not_called()


def test_get_locks_route(views, client, monkeypatch):
    monkeypatch.setattr(views, "get_round_locks",
                        lambda e: {"round1": {"locked": True}, "round2": {"locked": False}})
    res = client.get("/api/judge/locks/fall-2026", headers=HEADERS)
    assert res.status_code == 200
    assert res.get_json()["locks"]["round1"]["locked"] is True


def test_set_lock_route_validates_and_delegates(views, client, monkeypatch):
    setter = MagicMock(return_value={"success": True, "lock": {"locked": True}})
    monkeypatch.setattr(views, "set_round_lock", setter)
    monkeypatch.setattr(views, "_actor_display_name", lambda uid: "Greg")

    assert client.post("/api/judge/admin/locks/fall-2026/round3", json={"locked": True},
                       headers=HEADERS).status_code == 400
    assert client.post("/api/judge/admin/locks/fall-2026/round1", json={"locked": "yes"},
                       headers=HEADERS).status_code == 400
    setter.assert_not_called()

    res = client.post("/api/judge/admin/locks/fall-2026/round2", json={"locked": True}, headers=HEADERS)
    assert res.status_code == 200
    setter.assert_called_once_with("fall-2026", "round2", True, actor_id="judge-1", actor_name="Greg")


def test_progress_route(views, client, monkeypatch):
    monkeypatch.setattr(views, "get_judging_progress", lambda e, r: {"judges": [], "teams": [], "summary": {}})
    assert client.get("/api/judge/admin/progress/fall-2026/round2", headers=HEADERS).status_code == 200
    assert client.get("/api/judge/admin/progress/fall-2026/nope", headers=HEADERS).status_code == 400


def test_bulk_assignments_route_validates_and_delegates(views, client, monkeypatch):
    bulk = MagicMock(return_value={"success": True, "created": 2})
    monkeypatch.setattr(views, "bulk_create_judge_assignments", bulk)

    assert client.post("/api/judge/admin/assignments/bulk", json={"event_id": "e", "round": "round1"},
                       headers=HEADERS).status_code == 400
    assert client.post("/api/judge/admin/assignments/bulk",
                       json={"event_id": "e", "round": "round1", "panel_id": "p", "judge_ids": "j1"},
                       headers=HEADERS).status_code == 400
    bulk.assert_not_called()

    body = {"event_id": "e", "round": "round2", "panel_id": "p", "judge_ids": ["j1"], "team_ids": ["t1"],
            "room": "Main", "demo_times": {"t1": "2026-10-11T22:00:00-07:00"}, "replace": True}
    res = client.post("/api/judge/admin/assignments/bulk", json=body, headers=HEADERS)
    assert res.status_code == 200
    bulk.assert_called_once_with(event_id="e", round_name="round2", panel_id="p", judge_ids=["j1"],
                                 team_ids=["t1"], room="Main", demo_times={"t1": "2026-10-11T22:00:00-07:00"},
                                 replace=True)
