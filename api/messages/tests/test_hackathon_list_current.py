"""
get_hackathon_list "current" / "previous" grace day.

The backend runs on UTC, so a naive "today" flips at 5 PM Phoenix time and
an event vanished from the homepage on the evening of its end date. The
current list therefore keeps an event through the day after its end date
(grace day), and the previous list starts strictly after that cutoff so the
two lists never overlap (the original code already overlapped on the end
date itself: an event ending today satisfied both branches).

Tests freeze "now" (no dependence on the real current date) and drive the
real get_hackathon_list against a fake Firestore query that applies the
where() bounds, so the assertions check the actual filter semantics.
"""
import asyncio
import os

# ratelimiter 1.2.0 (pinned in requirements.txt) uses asyncio.coroutine,
# removed in Python 3.11. CI runs Python 3.10 where it still exists; this
# shim is a no-op there and only lets the import succeed on newer Pythons.
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = lambda func: func  # noqa: E731

os.environ.setdefault("ENVIRONMENT", "test")

from datetime import datetime, timezone  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

import services.hackathons_service as hs  # noqa: E402

# Frozen "now": 2026-11-12 noon UTC. The grace cutoff is therefore
# 2026-11-11 (one day earlier).
FROZEN_NOW = datetime(2026, 11, 12, 12, 0, 0)


class _FrozenDatetime(datetime):
    """datetime frozen at FROZEN_NOW; patched over hs.datetime in tests."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_NOW
        return FROZEN_NOW.replace(tzinfo=timezone.utc).astimezone(tz)


def _condition_matches(doc_dict, field, op, value):
    actual = doc_dict.get(field)
    if actual is None:
        return False
    if op == ">=":
        return actual >= value
    if op == "<":
        return actual < value
    if op == "<=":
        return actual <= value
    if op == "==":
        return actual == value
    raise AssertionError(f"unexpected operator {op!r}")


class _FakeQuery:
    """Stand-in for a Firestore query that applies where() bounds.

    Like real Firestore, where()/order_by()/limit() return a NEW query
    object, so two get_hackathon_list calls in one test cannot leak
    conditions into each other. where_calls is shared so tests can inspect
    the bounds the production code asked for.
    """

    def __init__(self, docs, conditions=None, calls=None, limit_value=None):
        self._docs = list(docs)
        self._conditions = list(conditions or [])
        self.where_calls = calls if calls is not None else []
        self.limit_value = limit_value

    def _clone(self, conditions=None, limit_value=None):
        return _FakeQuery(
            self._docs,
            conditions=self._conditions if conditions is None else conditions,
            calls=self.where_calls,
            limit_value=self.limit_value if limit_value is None else limit_value,
        )

    def where(self, *args, **kwargs):
        self.where_calls.append((args, kwargs))
        if "filter" in kwargs:
            f = kwargs["filter"]
            cond = (f.field_path, f.op_string, f.value)
        else:
            cond = tuple(args)
        return self._clone(conditions=self._conditions + [cond])

    def order_by(self, *args, **kwargs):
        return self._clone()

    def limit(self, n):
        return self._clone(limit_value=n)

    def stream(self):
        docs = [
            d
            for d in self._docs
            if all(_condition_matches(d.to_dict(), *c) for c in self._conditions)
        ]
        if self.limit_value is not None:
            docs = docs[: self.limit_value]
        return docs


def _fake_doc(event_id, end_date):
    snap = MagicMock(spec=hs.DocumentSnapshot)
    snap.id = event_id
    snap.to_dict.return_value = {
        "event_id": event_id,
        "title": event_id,
        "end_date": end_date,
    }
    return snap


def _four_events():
    # Frozen "now" is 2026-11-12; grace cutoff is 2026-11-11.
    return [
        _fake_doc("ends-tomorrow", "2026-11-13"),
        _fake_doc("ends-today", "2026-11-12"),
        _fake_doc("ends-yesterday", "2026-11-11"),
        _fake_doc("ends-two-days-ago", "2026-11-10"),
    ]


@pytest.fixture(autouse=True)
def _clear_list_cache():
    hs.get_hackathon_list.cache_clear()
    yield
    hs.get_hackathon_list.cache_clear()


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(hs, "datetime", _FrozenDatetime)
    return FROZEN_NOW


@pytest.fixture
def fake_query(monkeypatch):
    query = _FakeQuery(_four_events())
    db = MagicMock()
    db.collection.return_value = query
    monkeypatch.setattr(hs, "_get_db", lambda: db)
    return query


def _event_ids(result):
    return sorted(e["event_id"] for e in result["hackathons"])


def test_current_includes_today_yesterday_and_tomorrow(frozen_time, fake_query):
    """(a) ending today is current; (d) ending tomorrow is current."""
    result = hs.get_hackathon_list("current")
    assert _event_ids(result) == ["ends-today", "ends-tomorrow", "ends-yesterday"]


def test_current_keeps_yesterday_on_grace_day(frozen_time, fake_query):
    """(b) ending yesterday is STILL current (grace day)."""
    result = hs.get_hackathon_list("current")
    assert "ends-yesterday" in _event_ids(result)


def test_previous_returns_only_two_days_ago(frozen_time, fake_query):
    """(c) ending 2 days ago is previous only, not current."""
    result = hs.get_hackathon_list("previous")
    assert _event_ids(result) == ["ends-two-days-ago"]


def test_current_and_previous_are_disjoint_and_cover_all(frozen_time, fake_query):
    """No event is in both lists; together they cover every event."""
    current = set(_event_ids(hs.get_hackathon_list("current")))
    previous = set(_event_ids(hs.get_hackathon_list("previous")))
    assert current & previous == set()
    assert current | previous == {
        "ends-today",
        "ends-tomorrow",
        "ends-yesterday",
        "ends-two-days-ago",
    }


def test_current_query_uses_grace_cutoff(frozen_time, fake_query):
    """The current query bound is today minus one day, not today."""
    hs.get_hackathon_list("current")
    (args, kwargs) = fake_query.where_calls[0]
    assert args == ()
    assert kwargs["filter"].field_path == "end_date"
    assert kwargs["filter"].op_string == ">="
    assert kwargs["filter"].value == "2026-11-11"


def test_previous_query_uses_strict_grace_cutoff(frozen_time, fake_query):
    """The previous query bound is strictly earlier, so lists stay disjoint."""
    hs.get_hackathon_list("previous")
    (args, kwargs) = fake_query.where_calls[0]
    assert kwargs == {}
    assert args == ("end_date", "<", "2026-11-11")
