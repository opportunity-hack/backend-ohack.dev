"""GET /api/problem-statements/<id>/helpers service path + toggle dedupe.

Real problem statement docs carry the same person several times because the
legacy toggle appended on every click. The roster must collapse those, keep
the earliest "since", reflect the latest role, and batch-enrich names.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.problem_statements_service as pss
import services.users_service as us
from db.db import get_db


ALICE = "alice111alice111alice111alice111"
BOB = "bob22222bob22222bob22222bob22222"
GHOST = "ghost333ghost333ghost333ghost333"  # helping entry, no user doc
PS_ID = "ps-roster-1"

PROPEL_ID = "propel-roster-unique"
ME = "meee4444meee4444meee4444meee4444"
ME_USER_ID = "oauth2|slack|T123-UME"


def _boom(_propel_id):
    raise AssertionError("OAuth round-trip must NOT be required when propel_id resolves")


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    db = get_db()
    db.collection("users").document(ALICE).set({
        "user_id": "oauth2|slack|T123-UALICE", "name": "Alice Ada", "nickname": "alice",
        "profile_image": "https://cdn/alice.png", "email_address": "alice@example.com",
    })
    db.collection("users").document(BOB).set({
        "user_id": "oauth2|google-oauth2|999", "name": "Bob Byte", "nickname": "bob",
        "profile_image": None, "email_address": "bob@example.com",
    })
    db.collection("users").document(ME).set({
        "user_id": ME_USER_ID, "name": "Me Myself", "nickname": "me", "propel_id": PROPEL_ID,
        "email_address": "me@example.com", "profile_image": "x", "badges": [], "teams": [],
    })
    db.collection("problem_statements").document(PS_ID).set({
        "title": "Roster Test Project",
        "slack_channel": "npo-roster-test",
        "helping": [
            # Alice clicked twice (legacy append) — one row, since = the earlier click
            {"user": ALICE, "slack_user": "oauth2|slack|T123-UALICE", "type": "hacker",
             "timestamp": "2025-05-31T01:36:06.796319"},
            {"user": ALICE, "slack_user": "oauth2|slack|T123-UALICE", "type": "hacker",
             "timestamp": "2025-05-31T01:31:16.494398"},
            # Bob signed up as a hacker, later switched to mentor → latest type wins
            {"user": BOB, "slack_user": "oauth2|google-oauth2|999", "type": "hacker",
             "timestamp": "2025-05-29T04:16:16.596851"},
            {"user": BOB, "slack_user": "oauth2|google-oauth2|999", "type": "mentor",
             "timestamp": "2025-06-05T07:32:43.543334"},
            # No user doc anymore — still counted, just nameless
            {"user": GHOST, "slack_user": "oauth2|slack|T123-UGHOST", "type": "hacker",
             "timestamp": "2025-06-01T00:00:00"},
            # Junk that must not crash the roster
            {}, "not-a-dict", {"type": "hacker"},
        ],
    })
    pss.clear_helpers_cache()
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", _boom)
    monkeypatch.setattr(pss, "send_slack", lambda *a, **k: None)
    monkeypatch.setattr(pss, "send_slack_audit", lambda *a, **k: None)
    monkeypatch.setattr(pss, "invite_user_to_channel", lambda *a, **k: None)
    yield
    pss.clear_helpers_cache()


def _helping_list():
    return (get_db().collection("problem_statements").document(PS_ID).get().to_dict() or {}).get("helping", [])


def _mine(db_id):
    # The seed deliberately contains junk entries ({} / strings) — skip them
    return [h for h in _helping_list() if isinstance(h, dict) and h.get("user") == db_id]


def test_normalize_dedupes_keeps_earliest_since_and_latest_type():
    records = pss.normalize_helping_entries(
        (get_db().collection("problem_statements").document(PS_ID).get().to_dict() or {}).get("helping")
    )
    by_id = {r["db_id"]: r for r in records}
    assert set(by_id) == {ALICE, BOB, GHOST}
    assert by_id[ALICE]["since"] == "2025-05-31T01:31:16.494398"
    assert by_id[ALICE]["type"] == "hacker"
    assert by_id[BOB]["since"] == "2025-05-29T04:16:16.596851"
    assert by_id[BOB]["type"] == "mentor"
    # oldest first
    assert [r["db_id"] for r in records] == [BOB, ALICE, GHOST]


def test_roster_is_enriched_counted_and_public_safe():
    roster = pss.get_problem_statement_helpers(PS_ID)
    assert roster["problem_statement_id"] == PS_ID
    assert roster["slack_channel"] == "npo-roster-test"
    assert roster["counts"] == {"hacker": 2, "mentor": 1, "total": 3}

    by_id = {h["db_id"]: h for h in roster["helpers"]}
    assert by_id[ALICE]["name"] == "Alice Ada"
    assert by_id[ALICE]["profile_image"] == "https://cdn/alice.png"
    assert by_id[ALICE]["user_id"] == "oauth2|slack|T123-UALICE"
    assert by_id[BOB]["name"] == "Bob Byte"
    assert by_id[GHOST]["name"] is None and by_id[GHOST]["nickname"] is None

    # Nothing beyond the roster contract leaks (no email, propel_id, ...)
    for helper in roster["helpers"]:
        assert set(helper) == {"db_id", "user_id", "type", "since", "name", "nickname", "profile_image"}


def test_roster_unknown_problem_statement_is_none():
    assert pss.get_problem_statement_helpers("nope-does-not-exist") is None


def test_roster_is_cached_until_cleared():
    first = pss.get_problem_statement_helpers(PS_ID)
    assert first["counts"]["total"] == 3
    get_db().collection("problem_statements").document(PS_ID).update({"helping": []})
    assert pss.get_problem_statement_helpers(PS_ID)["counts"]["total"] == 3  # cached
    pss.clear_helpers_cache(PS_ID)
    assert pss.get_problem_statement_helpers(PS_ID)["counts"]["total"] == 0


def test_toggle_is_idempotent_and_keeps_original_since():
    payload = {"status": "helping", "problem_statement_id": PS_ID, "type": "hacker", "npo_id": "npo1"}
    assert pss.save_helping_status(PROPEL_ID, payload) is not None
    mine = _mine(ME)
    assert len(mine) == 1
    first_ts = mine[0]["timestamp"]

    # Second click: no duplicate, timestamp preserved
    assert pss.save_helping_status(PROPEL_ID, payload) is not None
    mine = _mine(ME)
    assert len(mine) == 1 and mine[0]["timestamp"] == first_ts

    # Role switch updates in place, still one entry, same "since"
    assert pss.save_helping_status(PROPEL_ID, {**payload, "type": "mentor"}) is not None
    mine = _mine(ME)
    assert len(mine) == 1 and mine[0]["type"] == "mentor" and mine[0]["timestamp"] == first_ts

    # The roster reflects the write immediately (cache cleared by the toggle)
    roster = pss.get_problem_statement_helpers(PS_ID)
    me = next(h for h in roster["helpers"] if h["db_id"] == ME)
    assert me["type"] == "mentor" and me["since"] == first_ts and me["name"] == "Me Myself"

    # Other people's legacy duplicates are untouched by my toggle
    assert len(_mine(ALICE)) == 2


def test_toggle_off_removes_only_me():
    pss.save_helping_status(PROPEL_ID, {"status": "helping", "problem_statement_id": PS_ID, "type": "hacker"})
    pss.save_helping_status(PROPEL_ID, {"status": "not_helping", "problem_statement_id": PS_ID, "type": ""})
    assert _mine(ME) == []
    assert pss.get_problem_statement_helpers(PS_ID)["counts"]["total"] == 3
