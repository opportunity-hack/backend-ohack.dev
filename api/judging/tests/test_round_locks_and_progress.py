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
    monkeypatch.setattr(svc, "_locks_db", lambda: _Db(stores))
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
    monkeypatch.setattr(svc, "_locks_db", lambda: Boom())
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
