"""giveaways_service: weight math, entry upsert, the closed/expired gate,
prize validation, and the draw's record/undo/reset rules.

A tiny in-memory Firestore stand-in (FakeDb) replaces `_get_db`, the user
resolver and the GitHub lookup are stubbed, and `_in_transaction` runs the
body against a FakeTransaction — nothing touches Firestore, PropelAuth,
GitHub or Slack.
"""
import os
import types
from copy import deepcopy
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import api.giveaways.giveaways_service as svc


# ---------------------------------------------------------------------------
# Fake Firestore
# ---------------------------------------------------------------------------

class Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return deepcopy(self._data) if self._data is not None else None


class DocRef:
    def __init__(self, db, collection, doc_id):
        self.db, self.collection, self.id = db, collection, doc_id

    @property
    def _key(self):
        return (self.collection, self.id)

    def get(self):
        self.db.reads.append(self.collection)
        return Snap(self.id, self.db.store.get(self._key))

    def set(self, data, merge=False):
        if merge and self.db.store.get(self._key) is not None:
            merged = dict(self.db.store[self._key])
            merged.update(deepcopy(data))  # top-level merge is enough here
            self.db.store[self._key] = merged
        else:
            self.db.store[self._key] = deepcopy(data)

    def delete(self):
        self.db.store.pop(self._key, None)


class Query:
    def __init__(self, db, collection, filters=()):
        self.db, self.collection, self.filters = db, collection, tuple(filters)

    def where(self, field, op, value):
        assert op == "=="
        return Query(self.db, self.collection, self.filters + ((field, value),))

    def document(self, doc_id):
        return DocRef(self.db, self.collection, doc_id)

    def stream(self):
        self.db.reads.append(self.collection)
        for (col, doc_id), data in list(self.db.store.items()):
            if col != self.collection or data is None:
                continue
            if all(data.get(f) == v for f, v in self.filters):
                yield Snap(doc_id, data)


class FakeDb:
    def __init__(self):
        self.store = {}
        self.reads = []

    def collection(self, name):
        return Query(self, name)


class FakeTransaction:
    def get(self, ref):
        return iter([ref.get()])

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


def _user(db_id="u1", **fields):
    base = dict(
        id=db_id, name="Ada Lovelace", nickname="Ada", profile_image="https://x/ada.png",
        github="ada", email_address="ada@example.com",
    )
    base.update(fields)
    return types.SimpleNamespace(**base)


FUTURE = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


@pytest.fixture
def env(monkeypatch):
    db = FakeDb()
    state = {
        "users": {"prop-1": _user("u1", role="hacker", city="Tempe"), "prop-2": _user("u2", github="", role="mentor")},
        "github": {"ada": [{"commits": 30, "pull_requests": {"total": 10}, "issues": {"total": 5}, "reviews": 2}]},
        "github_fail": False,
        "resolve_calls": 0,
        "audit": [],
    }

    def resolve(pid):
        state["resolve_calls"] += 1
        return state["users"].get(pid)

    def github(login):
        if state["github_fail"]:
            raise RuntimeError("github down")
        return state["github"].get(login, [])

    monkeypatch.setattr(svc, "_get_db", lambda: db)
    monkeypatch.setattr(svc, "_resolve_user", resolve)
    monkeypatch.setattr(svc, "_github_history", github)
    monkeypatch.setattr(svc, "_in_transaction", lambda _db, body: body(FakeTransaction()))
    monkeypatch.setattr(svc, "_audit", lambda action, message, payload=None: state["audit"].append(action))
    ids = iter(f"id{i}" for i in range(1000))
    monkeypatch.setattr(svc, "_new_id", lambda: next(ids))
    svc.clear_cache()
    yield db, state
    svc.clear_cache()


def _campaign(db, gid="fall-2026", **over):
    doc = {
        "title": "Fall giveaway", "description": "", "status": "open", "must_be_present": True,
        "closes_at": FUTURE, "draw": None, "created_at": "2026-11-01T00:00:00+00:00",
        "prizes": [
            {"id": "p1", "name": "Headphones", "quantity": 1, "sort_order": 0},
            {"id": "p2", "name": "Keyboard", "quantity": 2, "sort_order": 1},
        ],
    }
    doc.update(over)
    db.store[("giveaway_campaigns", gid)] = doc
    svc.clear_cache()
    return doc


# ---------------------------------------------------------------------------
# Weight
# ---------------------------------------------------------------------------

def test_weight_counts_filled_fields_and_caps_github():
    entries, b = svc.compute_weight({"role": "hacker", "github": "ada", "city": " ", "expertise": []}, 120)
    assert b["profile_fields"] == 2
    assert b["profile_entries"] == 10
    assert b["github_entries"] == 45 and b["github_contributions"] == 120
    assert entries == 55


def test_github_count_sums_every_kind_and_tolerates_shapes():
    rows = [
        {"commits": 3, "pull_requests": {"total": 2}, "issues": {"total": 1}, "reviews": 1},
        {"commits": 1, "pull_requests": 4, "issues": None, "reviews": "x"},
        "junk",
    ]
    assert svc.github_contribution_count(rows) == 12


def test_profile_fields_mirror_frontend_list():
    # src/components/Profile/profileFields.js has 17 fields — keep in sync.
    assert len(svc.GIVEAWAY_PROFILE_FIELDS) == 17
    assert len(set(svc.GIVEAWAY_PROFILE_FIELDS)) == 17


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def test_save_entry_computes_weight_on_server(env):
    db, _ = env
    _campaign(db)
    out = svc.save_entry("prop-1", "fall-2026", {"all_prizes": False, "prize_ids": ["p2"], "entries": 99999})
    entry = out["entry"]
    # role, github, city, propel_id? (not set) -> role + github + city = 3 fields
    assert entry["breakdown"]["profile_fields"] == 3
    assert entry["breakdown"]["github_entries"] == 45  # 47 contributions, capped
    assert entry["entries"] == 15 + 45
    assert entry["prize_ids"] == ["p2"] and entry["all_prizes"] is False
    stored = db.store[("giveaway_entries", "fall-2026__u1")]
    assert stored["user"]["name"] == "Ada Lovelace"
    assert out["created"] is True


def test_upsert_keeps_created_at_and_one_doc(env, monkeypatch):
    db, _ = env
    _campaign(db)
    svc.save_entry("prop-1", "fall-2026", {"prize_ids": ["p1"]})
    first = db.store[("giveaway_entries", "fall-2026__u1")]["created_at"]
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(svc, "_now", lambda: later)
    out = svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    doc = db.store[("giveaway_entries", "fall-2026__u1")]
    assert doc["created_at"] == first
    assert doc["updated_at"] == later.isoformat() != first
    assert doc["all_prizes"] is True and doc["prize_ids"] == []
    assert out["created"] is False
    assert len([k for k in db.store if k[0] == "giveaway_entries"]) == 1


@pytest.mark.parametrize("over", [{"status": "closed"}, {"status": "drawn"}, {"closes_at": PAST}])
def test_closed_or_expired_rejects_entry_and_withdraw(env, over):
    db, _ = env
    _campaign(db, **over)
    with pytest.raises(svc.GiveawayError) as e:
        svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    assert (e.value.status, e.value.error) == (409, "giveaway_closed")
    with pytest.raises(svc.GiveawayError) as e:
        svc.withdraw_entry("prop-1", "fall-2026")
    assert e.value.status == 409


def test_draft_and_missing_are_404(env):
    db, _ = env
    _campaign(db, status="draft")
    for gid in ("fall-2026", "nope"):
        with pytest.raises(svc.GiveawayError) as e:
            svc.save_entry("prop-1", gid, {"all_prizes": True})
        assert e.value.status == 404


def test_invalid_and_missing_prizes(env):
    db, _ = env
    _campaign(db)
    with pytest.raises(svc.GiveawayError) as e:
        svc.save_entry("prop-1", "fall-2026", {"prize_ids": ["p1", "ghost"]})
    assert e.value.error == "invalid_prizes"
    with pytest.raises(svc.GiveawayError) as e:
        svc.save_entry("prop-1", "fall-2026", {"prize_ids": []})
    assert e.value.error == "no_prizes_selected"


def test_campaign_without_prizes_enters_everything(env):
    db, _ = env
    _campaign(db, prizes=[])
    out = svc.save_entry("prop-1", "fall-2026", {"all_prizes": False, "prize_ids": []})
    assert out["entry"]["all_prizes"] is True


def test_github_failure_keeps_previous_github_entries(env):
    db, state = env
    _campaign(db)
    svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    state["github_fail"] = True
    out = svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    assert out["entry"]["github_unavailable"] is True
    assert out["entry"]["breakdown"]["github_entries"] == 45


def test_github_failure_without_history_is_zero_not_error(env):
    db, state = env
    _campaign(db)
    state["github_fail"] = True
    out = svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    assert out["entry"]["breakdown"]["github_entries"] == 0
    assert out["entry"]["github_unavailable"] is True


def test_withdraw_deletes_entry(env):
    db, _ = env
    _campaign(db)
    svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    assert svc.withdraw_entry("prop-1", "fall-2026") == {"withdrawn": True}
    assert ("giveaway_entries", "fall-2026__u1") not in db.store
    assert svc.withdraw_entry("prop-1", "fall-2026") == {"withdrawn": False}


def test_open_lists_open_plus_entered_closed_with_win(env):
    db, _ = env
    _campaign(db, "fall-2026")
    _campaign(db, "spring-2026", status="drawn", draw={"seed": "1", "results": [
        {"id": "r1", "prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won"},
    ]})
    _campaign(db, "old-2025", status="closed")
    _campaign(db, "draft-1", status="draft")
    db.store[("giveaway_entries", "spring-2026__u1")] = {
        "giveaway_id": "spring-2026", "user_id": "u1", "all_prizes": True, "entries": 10,
        "breakdown": {"github_entries": 5}, "updated_at": "2026-04-01",
    }
    out = svc.get_open_giveaways("prop-1")
    ids = [g["id"] for g in out["giveaways"]]
    assert ids == ["fall-2026", "spring-2026"]
    spring = out["giveaways"][1]
    assert spring["my_entry"]["entries"] == 10
    assert spring["my_win"]["name"] == "Headphones"
    assert out["giveaways"][0]["my_entry"] is None
    assert out["my_weight"]["entries"] == 60
    assert "draw" not in out["giveaways"][0]


def test_open_503_when_user_unresolvable(env):
    with pytest.raises(svc.GiveawayError) as e:
        svc.get_open_giveaways("ghost")
    assert e.value.status == 503


# ---------------------------------------------------------------------------
# Admin campaigns
# ---------------------------------------------------------------------------

def test_create_validates_and_mints_prize_ids(env):
    db, state = env
    out = svc.admin_create_campaign({
        "id": "fall-2026", "title": "Fall", "prizes": [{"name": "Mug", "quantity": 3}],
        "closes_at": "2026-11-14T16:00:00-07:00",
    }, "admin@x")
    assert out["status"] == "draft"
    assert out["prizes"][0]["id"] == "id0" and out["prizes"][0]["quantity"] == 3
    assert "giveaway_create" in state["audit"]
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_create_campaign({"id": "fall-2026", "title": "Again"}, "admin@x")
    assert e.value.status == 409


@pytest.mark.parametrize("body", [
    {"id": "Bad Id", "title": "x"},
    {"id": "ok-id", "title": ""},
    {"id": "ok-id", "title": "x", "closes_at": "2026-11-14T16:00:00"},
    {"id": "ok-id", "title": "x", "prizes": [{"name": ""}]},
    {"id": "ok-id", "title": "x", "prizes": [{"name": "a", "quantity": 0}]},
    {"id": "ok-id", "title": "x", "prizes": [{"name": "a", "product_url": "javascript:alert(1)"}]},
    {"id": "ok-id", "title": "x", "status": "drawn"},
])
def test_create_rejects_bad_input(env, body):
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_create_campaign(body, "admin@x")
    assert e.value.status == 400


def test_update_cannot_remove_or_shrink_prize_with_winner(env):
    db, _ = env
    _campaign(db, draw={"seed": "1", "results": [
        {"id": "r", "prize_id": "p2", "slot": 1, "user_id": "u1", "outcome": "won"},
    ]}, status="closed")
    keep_p1 = [{"id": "p1", "name": "Headphones"}]
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_update_campaign("fall-2026", {"prizes": keep_p1}, "a")
    assert e.value.error == "prize_has_winner"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_update_campaign("fall-2026", {"prizes": keep_p1 + [{"id": "p2", "name": "K", "quantity": 1}]}, "a")
    assert e.value.error == "prize_has_winner"
    out = svc.admin_update_campaign("fall-2026", {"prizes": [{"id": "p2", "name": "K", "quantity": 2}]}, "a")
    assert [p["id"] for p in out["prizes"]] == ["p2"]


def test_status_locked_once_draw_started(env):
    db, _ = env
    _campaign(db, status="closed", draw={"seed": "1", "results": []})
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_update_campaign("fall-2026", {"status": "open"}, "a")
    assert e.value.error == "draw_started"


def test_entries_route_reads_only_entries(env):
    db, state = env
    _campaign(db)
    svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    svc.save_entry("prop-2", "fall-2026", {"prize_ids": ["p1"]})
    db.store[("giveaway_entries", "other__u1")] = {"giveaway_id": "other", "user_id": "u1"}
    db.reads.clear()
    calls = state["resolve_calls"]
    out = svc.admin_list_entries("fall-2026")
    assert out["count"] == 2
    assert [e["user_id"] for e in out["entries"]] == ["u1", "u2"]
    assert db.reads == ["giveaway_entries"]
    assert state["resolve_calls"] == calls


# ---------------------------------------------------------------------------
# Draw
# ---------------------------------------------------------------------------

@pytest.fixture
def drawing(env):
    db, state = env
    _campaign(db)
    svc.save_entry("prop-1", "fall-2026", {"all_prizes": True})
    svc.save_entry("prop-2", "fall-2026", {"prize_ids": ["p2"]})
    svc.admin_start_draw("fall-2026", {"seed": "482913"}, "admin@x")
    return db, state


def test_start_draw_locks_entries_and_is_idempotent(drawing):
    db, state = drawing
    doc = db.store[("giveaway_campaigns", "fall-2026")]
    assert doc["status"] == "closed" and doc["draw"]["seed"] == "482913"
    assert svc.admin_start_draw("fall-2026", {"seed": "482913"}, "a")["draw"]["seed"] == "482913"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_start_draw("fall-2026", {"seed": "999"}, "a")
    assert e.value.error == "draw_already_started"
    assert "giveaway_draw_start" in state["audit"]


def test_record_result_requires_started_draw(env):
    db, _ = env
    _campaign(db)
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won"}, "a")
    assert e.value.error == "draw_not_started"


def test_one_win_per_person_and_slot(drawing):
    out = svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won"}, "a")
    assert out["result"]["name"] == "Ada Lovelace"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_record_result("fall-2026", {"prize_id": "p2", "slot": 0, "user_id": "u1", "outcome": "won"}, "a")
    assert e.value.error == "already_won"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u2", "outcome": "won"}, "a")
    # u2 didn't enter p1 at all
    assert e.value.error == "not_entered"


def test_slot_filled_and_slot_range(drawing):
    db, state = drawing
    state["users"]["prop-3"] = _user("u3", github="", role="judge")
    db.store[("giveaway_campaigns", "fall-2026")]["status"] = "open"
    db.store[("giveaway_campaigns", "fall-2026")]["draw"] = None
    svc.clear_cache()
    svc.save_entry("prop-3", "fall-2026", {"all_prizes": True})
    svc.admin_start_draw("fall-2026", {"seed": "1"}, "a")
    svc.admin_record_result("fall-2026", {"prize_id": "p2", "slot": 0, "user_id": "u2", "outcome": "won"}, "a")
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_record_result("fall-2026", {"prize_id": "p2", "slot": 0, "user_id": "u3", "outcome": "won"}, "a")
    assert e.value.error == "slot_filled"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_record_result("fall-2026", {"prize_id": "p2", "slot": 2, "user_id": "u3", "outcome": "won"}, "a")
    assert e.value.error == "invalid_slot"


def test_absent_then_undo_then_all_won_marks_drawn(drawing):
    db, state = drawing
    absent = svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "absent"}, "a")
    assert absent["status"] == "closed"
    undone = svc.admin_undo_result("fall-2026", {"result_id": absent["result"]["id"]}, "a")
    assert undone["draw"]["results"] == []
    svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won"}, "a")
    svc.admin_record_result("fall-2026", {"prize_id": "p2", "slot": 0, "user_id": "u2", "outcome": "won"}, "a")
    assert db.store[("giveaway_campaigns", "fall-2026")]["status"] == "closed"  # p2 slot 1 still open
    out = svc.admin_record_result("fall-2026", {"finish": True}, "a")
    assert out["status"] == "drawn" and out["result"] is None
    assert state["audit"].count("giveaway_winner") == 2


def test_undo_reopens_a_drawn_giveaway(drawing):
    db, _ = drawing
    won = svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won", "finish": True}, "a")
    assert won["status"] == "drawn"
    out = svc.admin_undo_result("fall-2026", {"result_id": won["result"]["id"]}, "a")
    assert out["status"] == "closed"
    with pytest.raises(svc.GiveawayError) as e:
        svc.admin_undo_result("fall-2026", {"result_id": "nope"}, "a")
    assert e.value.status == 404


def test_reset_clears_draw(drawing):
    db, state = drawing
    svc.admin_record_result("fall-2026", {"prize_id": "p1", "slot": 0, "user_id": "u1", "outcome": "won"}, "a")
    out = svc.admin_reset_draw("fall-2026", "a")
    assert out["draw"] is None and out["status"] == "closed"
    assert "giveaway_draw_reset" in state["audit"]
    # A new seed is accepted after a reset.
    assert svc.admin_start_draw("fall-2026", {"seed": "777"}, "a")["draw"]["seed"] == "777"
