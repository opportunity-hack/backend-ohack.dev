"""
Round locks, judge progress and bulk assignments (Oct 2026).

Locks are stored in `judging_round_locks` and read uncached on every judge
write; progress is computed server-side from assignments + scores; bulk
assignment creation is idempotent. Service-level tests patch the db layer
exactly like the sibling judging tests do.
"""
import os
from datetime import datetime

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import pytest

import api.judging.judging_service as svc


# ---------------------------------------------------------------------------
# Tiny in-memory doc store standing in for the locks collection
# ---------------------------------------------------------------------------

class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _DocRef:
    def __init__(self, store, doc_id):
        self._store, self._id = store, doc_id

    def get(self):
        return _Snap(self._id, self._store.get(self._id))

    def set(self, data, merge=False):
        if merge and self._id in self._store:
            self._store[self._id].update(data)
        else:
            self._store[self._id] = dict(data)


class _Collection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return _DocRef(self._store, doc_id)


class _Db:
    def __init__(self, stores):
        self._stores = stores

    def collection(self, name):
        return _Collection(self._stores.setdefault(name, {}))


@pytest.fixture
def lock_store(monkeypatch):
    stores = {}
    monkeypatch.setattr(svc, "_db", lambda: _Db(stores))
    monkeypatch.setattr("common.utils.slack.send_slack_audit", lambda **kw: None, raising=False)
    return stores


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

def test_lock_defaults_to_unlocked(lock_store):
    assert svc.is_round_locked("fall-2026", "round1") is False
    locks = svc.get_round_locks("fall-2026")
    assert set(locks) == {"round1", "round2"}
    assert locks["round2"]["locked"] is False


def test_set_lock_roundtrip_and_unlock_clears_actor(lock_store):
    res = svc.set_round_lock("fall-2026", "round2", True, actor_id="p-1", actor_name="Greg")
    assert res["success"] and res["lock"]["locked"] is True
    assert res["lock"]["locked_by_name"] == "Greg"
    assert svc.is_round_locked("fall-2026", "round2") is True
    # round1 untouched
    assert svc.is_round_locked("fall-2026", "round1") is False

    res = svc.set_round_lock("fall-2026", "round2", False, actor_id="p-1", actor_name="Greg")
    assert res["lock"]["locked"] is False
    assert res["lock"]["locked_by"] is None and res["lock"]["locked_at"] is None


def test_set_lock_rejects_bad_round_and_non_bool(lock_store):
    assert svc.set_round_lock("fall-2026", "round3", True)["success"] is False
    assert svc.set_round_lock("fall-2026", "round1", "yes")["success"] is False


def test_lock_read_error_fails_open(monkeypatch):
    class Boom:
        def collection(self, *_):
            raise RuntimeError("firestore down")
    monkeypatch.setattr(svc, "_db", lambda: Boom())
    assert svc.is_round_locked("fall-2026", "round1") is False


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def _assignment(judge_id, team_id, round_name="round1", panel_id="p1"):
    return {"id": f"{judge_id}-{team_id}", "judge_id": judge_id, "team_id": team_id,
            "round": round_name, "panel_id": panel_id, "event_id": "fall-2026"}


def _score(judge_id, team_id, is_draft, round_name="round1", when="2026-10-10T20:00:00+00:00"):
    return {"judge_id": judge_id, "team_id": team_id, "round": round_name, "is_draft": is_draft,
            "submitted_at": None if is_draft else when, "updated_at": when}


def test_progress_classifies_submitted_draft_and_missing(lock_store):
    assignments = {"data": [
        _assignment("j1", "t1"), _assignment("j1", "t2"), _assignment("j1", "t3"),
        _assignment("j2", "t1"), _assignment("j2", "t2"),
        _assignment("j2", "t9", round_name="round2"),  # other round — ignored
    ]}
    scores = {"data": [
        _score("j1", "t1", False),
        _score("j1", "t1", True),   # stale draft after submit — not "in progress"
        _score("j1", "t2", True),   # draft only
        _score("j2", "t1", False), _score("j2", "t2", False),
        _score("j3", "t1", False),  # judge no longer assigned → orphan
    ]}
    judges = {"data": [
        {"user_id": "j1", "name": "Ada", "email": "ada@x.org", "isSelected": True},
        {"user_id": "j2", "name": "Bob", "email": "bob@x.org", "isSelected": True},
    ]}
    teams = [{"id": "t1", "name": "Alpha"}, {"id": "t2", "name": "Beta"}, {"id": "t3", "name": "Gamma"}]

    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=assignments), \
            patch.object(svc, "fetch_judge_scores_by_event_id", return_value=scores), \
            patch.object(svc, "get_volunteer_from_db_by_event", return_value=judges), \
            patch.object(svc, "get_teams_batch", return_value=teams):
        result = svc.get_judging_progress("fall-2026", "round1")

    by_id = {j["judge_id"]: j for j in result["judges"]}
    ada, bob = by_id["j1"], by_id["j2"]
    assert ada["submitted_team_ids"] == ["t1"]
    assert ada["draft_team_ids"] == ["t2"]
    assert ada["missing_team_ids"] == ["t3"]
    assert ada["complete"] is False and ada["assigned"] == 3 and ada["submitted"] == 1
    assert bob["complete"] is True and bob["missing_team_ids"] == []
    # incomplete judges sort first
    assert result["judges"][0]["judge_id"] == "j1"

    teams_by = {t["team_id"]: t for t in result["teams"]}
    assert teams_by["t1"]["submitted"] == 2 and teams_by["t1"]["name"] == "Alpha"
    assert teams_by["t3"]["submitted"] == 0

    s = result["summary"]
    assert s["judges_total"] == 2 and s["judges_complete"] == 1
    assert s["assignments_total"] == 5 and s["submitted_total"] == 3 and s["drafts_total"] == 1
    assert s["orphan_submitted"] == 1
    assert result["lock"]["locked"] is False


def test_progress_rejects_unknown_round(lock_store):
    assert "error" in svc.get_judging_progress("fall-2026", "finals")


# ---------------------------------------------------------------------------
# Bulk assignments
# ---------------------------------------------------------------------------

def test_bulk_assign_creates_cross_product_once(lock_store):
    inserted, deleted = [], []
    existing = {"data": [_assignment("j1", "t1")]}  # already there → kept

    def _insert(a):
        inserted.append((a.judge_id, a.team_id, a.panel_id, a.round, a.room, a.demo_time))
        a.id = f"new-{len(inserted)}"
        return a

    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=existing), \
            patch.object(svc, "insert_judge_assignment", side_effect=_insert), \
            patch.object(svc, "update_judge_assignment", side_effect=lambda a: a), \
            patch.object(svc, "delete_judge_assignment", side_effect=lambda i: deleted.append(i) or True):
        res = svc.bulk_create_judge_assignments(
            "fall-2026", "round1", "p1", ["j1", "j2", "j2"], ["t1", "t2"], room="Room A")

    assert res["success"] is True
    assert res["created"] == 3 and res["kept"] == 1 and res["removed"] == 0 and res["total"] == 4
    pairs = sorted((i[0], i[1]) for i in inserted)
    assert pairs == [("j1", "t2"), ("j2", "t1"), ("j2", "t2")]
    assert all(i[2] == "p1" and i[3] == "round1" and i[4] == "Room A" for i in inserted)
    assert deleted == []


def test_bulk_assign_replace_prunes_pairs_outside_cross_product(lock_store):
    existing = {"data": [_assignment("j1", "t1"), _assignment("j1", "tOLD"), _assignment("jGONE", "t1"),
                         _assignment("j1", "t1", round_name="round2", panel_id="p1")]}  # other round untouched
    deleted = []
    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=existing), \
            patch.object(svc, "insert_judge_assignment", side_effect=lambda a: a), \
            patch.object(svc, "update_judge_assignment", side_effect=lambda a: a), \
            patch.object(svc, "delete_judge_assignment", side_effect=lambda i: deleted.append(i) or True):
        res = svc.bulk_create_judge_assignments("fall-2026", "round1", "p1", ["j1"], ["t1"], replace=True)

    assert res["removed"] == 2
    assert sorted(deleted) == ["j1-tOLD", "jGONE-t1"]


def test_bulk_assign_validates_inputs(lock_store):
    assert svc.bulk_create_judge_assignments("e", "round9", "p", ["j"], ["t"])["success"] is False
    assert svc.bulk_create_judge_assignments("e", "round1", None, ["j"], ["t"])["success"] is False
    too_many = [f"j{i}" for i in range(101)]  # dedupe keeps all 101 distinct ids
    assert svc.bulk_create_judge_assignments("e", "round1", "p", too_many, ["t"])["success"] is False


# ---------------------------------------------------------------------------
# Review follow-ups (Oct 2026): public lock view, failed reads, strict bulk input
# ---------------------------------------------------------------------------

def test_public_lock_view_never_exposes_the_locking_admin(lock_store):
    svc.set_round_lock("fall-2026", "round1", True, actor_id="propel-uuid", actor_name="Greg")
    public = svc.get_round_locks("fall-2026", public=True)["round1"]
    assert public["locked"] is True and public["locked_at"]
    assert "locked_by" not in public and "locked_by_name" not in public
    admin = svc.get_round_locks("fall-2026")["round1"]
    assert admin["locked_by"] == "propel-uuid"


def test_progress_fails_loudly_when_the_assignment_read_failed(lock_store):
    failed = {"data": [], "error": "deadline exceeded"}
    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=failed), \
            patch.object(svc, "fetch_judge_scores_by_event_id", return_value={"data": []}):
        result = svc.get_judging_progress("fall-2026", "round1")
    assert "error" in result and "judges" not in result


def test_bulk_assign_does_not_reinsert_when_the_existing_read_failed(lock_store):
    inserted = []
    failed = {"data": [], "error": "deadline exceeded"}
    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=failed), \
            patch.object(svc, "insert_judge_assignment", side_effect=lambda a: inserted.append(a) or a):
        res = svc.bulk_create_judge_assignments("fall-2026", "round1", "p1", ["j1"], ["t1"], replace=True)
    assert res["success"] is False
    assert inserted == []


def test_bulk_assign_rejects_empty_cross_product_and_bad_types(lock_store):
    assert svc.bulk_create_judge_assignments("e", "round1", "p", [], ["t"], replace=True)["error"] == "empty_cross_product"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", ["j"], [])["error"] == "empty_cross_product"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", ["j"], ["t"], replace="false")["error"] == "replace_must_be_boolean"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", [{"id": 1}], ["t"])["error"] == "ids_must_be_non_empty_strings"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", ["j"], [7])["error"] == "ids_must_be_non_empty_strings"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", ["j"], ["t"], room=5)["error"] == "room_must_be_string"
    assert svc.bulk_create_judge_assignments("e", "round1", "p", ["j"], ["t"], demo_times={"t": 5})["error"].startswith("demo_times")


def test_bulk_assign_replace_collapses_duplicate_docs(lock_store):
    existing = {"data": [
        {**_assignment("j1", "t1"), "id": "dup-a"},
        {**_assignment("j1", "t1"), "id": "dup-b"},
        {**_assignment("j1", "tOLD"), "id": "old-a"},
        {**_assignment("j1", "tOLD"), "id": "old-b"},
    ]}
    deleted = []
    with patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=existing), \
            patch.object(svc, "insert_judge_assignment", side_effect=lambda a: a), \
            patch.object(svc, "update_judge_assignment", side_effect=lambda a: a), \
            patch.object(svc, "delete_judge_assignment", side_effect=lambda i: deleted.append(i) or True):
        res = svc.bulk_create_judge_assignments("fall-2026", "round1", "p1", ["j1"], ["t1"], replace=True)
    assert res["kept"] == 1 and res["created"] == 0
    assert sorted(deleted) == ["dup-b", "old-a", "old-b"]
    assert res["removed"] == 3


# ---------------------------------------------------------------------------
# Round-scoped assignment check + batched member lookup + get_judge_teams shape
# ---------------------------------------------------------------------------

class _A:
    def __init__(self, team_id, round_name, demo_time=None, room=None):
        self.team_id, self.round, self.demo_time, self.room = team_id, round_name, demo_time, room


def test_is_judge_assigned_for_round_matches_team_and_round():
    with patch.object(svc, "fetch_judge_assignments_by_event_and_judge",
                      return_value=[_A("t1", "round1"), _A("t2", "round2")]):
        assert svc.is_judge_assigned_for_round("j", "e", "t1", "round1") is True
        assert svc.is_judge_assigned_for_round("j", "e", "t1", "round2") is False
        assert svc.is_judge_assigned_for_round("j", "e", "t2", "round2") is True
        assert svc.is_judge_assigned_for_round("j", "e", "t9", "round1") is False


class _UserSnap:
    def __init__(self, uid, data):
        self.id, self._data, self.exists = uid, data, data is not None

    def to_dict(self):
        return dict(self._data) if self._data else None


class _UsersDb:
    def __init__(self, users):
        self._users = users
        self.requested = []

    def collection(self, name):
        assert name == "users"
        return self

    def document(self, uid):
        return uid

    def get_all(self, refs):
        self.requested.append(list(refs))
        return [_UserSnap(uid, self._users.get(uid)) for uid in refs]


def test_batch_users_for_teams_one_round_trip_and_missing_docs_dropped(monkeypatch):
    db = _UsersDb({"u1": {"name": "Ada", "email_address": "ada@x.org", "profile_image": "img"},
                   "u2": {"name": "Bob"}})
    monkeypatch.setattr(svc, "_db", lambda: db)
    teams = [{"id": "t1", "users": [{"id": "u1"}, "u2", "u1"]}, {"id": "t2", "users": ["u3", {"id": "u2"}]}]
    users = svc._batch_users_for_teams(teams)
    assert db.requested == [["u1", "u2", "u3"]]  # deduped, ONE get_all
    assert users == {
        "u1": {"id": "u1", "name": "Ada", "email": "ada@x.org", "profile_image": "img"},
        "u2": {"id": "u2", "name": "Bob", "email": "", "profile_image": ""},
    }
    # Same member shape as the per-user path; missing u3 is dropped; in-team order kept.
    formatted = svc.format_team_for_judge(teams[0], users_by_id=users)
    assert [m["id"] for m in formatted["members"]] == ["u1", "u2", "u1"]
    assert formatted["members"][0] == users["u1"]
    assert svc.format_team_for_judge(teams[1], users_by_id=users)["members"] == [users["u2"]]


def test_batch_users_for_teams_falls_back_to_per_user_reads_on_failure(monkeypatch):
    class Boom:
        def collection(self, *_):
            raise RuntimeError("get_all down")
    monkeypatch.setattr(svc, "_db", lambda: Boom())
    assert svc._batch_users_for_teams([{"id": "t1", "users": ["u1"]}]) is None
    # None → format_team_for_judge takes the legacy per-user path.
    with patch("common.utils.firebase.get_user_by_id", return_value={"id": "u1", "name": "Ada"}) as m:
        out = svc.format_team_for_judge({"id": "t1", "users": ["u1"]}, users_by_id=None)
    m.assert_called_once_with("u1")
    assert out["members"][0]["name"] == "Ada"


def test_get_judge_teams_embeds_event_and_public_locks_and_prefers_round2_slot(lock_store, monkeypatch):
    svc.set_round_lock("fall-2026", "round2", True, actor_id="p", actor_name="Greg")
    assignments = [_A("t1", "round1"), _A("t1", "round2", demo_time="2026-10-11T23:00:00-07:00", room="Main")]
    monkeypatch.setattr(svc, "_batch_users_for_teams", lambda teams: {})
    with patch.object(svc, "fetch_judge_assignments_by_event_and_judge", return_value=assignments), \
            patch.object(svc, "get_teams_batch", return_value=[{"id": "t1", "name": "Alpha", "users": []}]), \
            patch.object(svc, "fetch_judge_scores_by_judge_and_event", return_value=[]), \
            patch.object(svc, "get_nonprofit_by_id", return_value={"name": "NP"}), \
            patch.object(svc, "get_single_hackathon_event", return_value={"title": "Fall 2026", "timezone": "America/Phoenix"}):
        result = svc.get_judge_teams("judge-1", "fall-2026")

    assert result["event"]["title"] == "Fall 2026"
    assert result["locks"]["round2"]["locked"] is True
    assert "locked_by" not in result["locks"]["round2"]
    assert result["round1_teams"][0]["demo_time"] is None
    r2 = result["round2_teams"][0]
    assert r2["demo_time"] == "2026-10-11T23:00:00-07:00" and r2["room"] == "Main"
