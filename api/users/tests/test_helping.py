"""Canonical helping toggle (POST /api/users/profile/helping service path).

Ports of the legacy behavior worth pinning: add/remove round-trip, the
exact-match removal fix (the legacy body used a substring test that could
remove OTHER users' entries), and OAuth-outage resilience via the resolver.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.problem_statements_service as pss
import services.users_service as us
from db.db import get_db


DB_ID = "help1111help1111help1111help1111"
PROPEL_ID = "propel-helping-unique"
USER_ID = "oauth2|slack|T123-UHELP1"
PS_ID = "ps-help-1"


def _boom(_propel_id):
    raise AssertionError("OAuth round-trip must NOT be required when propel_id resolves")


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    get_db().collection("users").document(DB_ID).set({
        "user_id": USER_ID,
        "email_address": "helper@example.com",
        "profile_image": "x",
        "name": "Helper One",
        "nickname": "Helper",
        "propel_id": PROPEL_ID,
        "badges": [],
        "teams": [],
    })
    get_db().collection("problem_statements").document(PS_ID).set({
        "title": "Helping Test Project",
        "slack_channel": "npo-helping-test",
        # Pre-existing entry from ANOTHER user whose id is a SUBSTRING of ours —
        # the legacy `not in` removal would have wrongly deleted this.
        "helping": [{"user": "help1111", "slack_user": "U0OTHER", "type": "hacker",
                     "timestamp": "2026-01-01T00:00:00"}],
    })
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    monkeypatch.setattr(pss, "send_slack", lambda *a, **k: None)
    monkeypatch.setattr(pss, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(pss, "invite_user_to_channel", lambda *a, **k: None)
    yield


def _helping_list():
    return (get_db().collection("problem_statements").document(PS_ID).get().to_dict() or {}).get("helping", [])


def test_helping_add_then_remove_roundtrip():
    result = pss.save_helping_status(PROPEL_ID, {
        "status": "helping", "problem_statement_id": PS_ID, "type": "hacker", "npo_id": "npo1",
    })
    assert result is not None
    mine = [h for h in _helping_list() if h["user"] == DB_ID]
    assert len(mine) == 1
    assert mine[0]["type"] == "hacker"
    assert mine[0]["slack_user"] == USER_ID

    result = pss.save_helping_status(PROPEL_ID, {
        "status": "not_helping", "problem_statement_id": PS_ID, "type": "hacker",
    })
    assert result is not None
    assert [h for h in _helping_list() if h["user"] == DB_ID] == []


def test_remove_is_exact_match_not_substring():
    pss.save_helping_status(PROPEL_ID, {
        "status": "not_helping", "problem_statement_id": PS_ID, "type": "hacker",
    })
    survivors = _helping_list()
    # The other user's entry (id "help1111", a substring of ours) must survive
    assert any(h["user"] == "help1111" for h in survivors), (
        "exact-match removal regressed to the legacy substring bug"
    )


def test_unknown_problem_statement_returns_none():
    result = pss.save_helping_status(PROPEL_ID, {
        "status": "helping", "problem_statement_id": "nope-does-not-exist", "type": "hacker",
    })
    assert result is None
