"""Tests for get_resend_email_statuses fetching uncached IDs in parallel.

The old version fetched one ID at a time with a 0.3s sleep before each
call, so a cold-cache batch of 100 took ~90 seconds (Sentry performance
report, p95 ~90s). The fix fetches uncached IDs with a ThreadPoolExecutor
while keeping the same result shape, ordering, per-ID retry behavior, and
cache TTLs. These tests pin that contract:

- every uncached ID is fetched exactly once, and results match what the
  sequential version returned, including its key order (cached entries
  first, then fetched ones, each in input order);
- fetches actually overlap (max in-flight > 1);
- cached IDs are never refetched;
- a failing ID is retried once and comes back as 'unknown', uncached;
- fetched results are cached with the terminal/transient TTLs.
"""
import os
import threading
import time

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.volunteers_service as vs


def _payload(eid, last_event="delivered"):
    return {
        "id": eid,
        "to": ["vol@example.com"],
        "subject": f"Subject {eid}",
        "created_at": "2026-10-01T00:00:00Z",
        "last_event": last_event,
    }


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("RESEND_EMAIL_STATUS_KEY", "re_test_key")


@pytest.fixture
def fake_cache(monkeypatch):
    """In-memory stand-in for the redis cache helpers the service uses."""
    store = {}
    sets = []

    def _set_cached(key, value, ttl=None):
        sets.append((key, value, ttl))
        store[key] = value

    monkeypatch.setattr(vs, "get_cached", lambda key: store.get(key))
    monkeypatch.setattr(vs, "set_cached", _set_cached)
    return store, sets


def _mock_get(monkeypatch, handler):
    """Replace resend.Emails.get with a thread-safe recorder."""
    calls = []
    lock = threading.Lock()

    def _get(eid):
        with lock:
            calls.append(eid)
        return handler(eid)

    monkeypatch.setattr(vs.resend.Emails, "get", _get)
    return calls


def test_multiple_ids_fetched_in_parallel_with_same_results(monkeypatch, fake_cache):
    ids = [f"re_{i}" for i in range(6)]
    in_flight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def handler(eid):
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.2)
        with lock:
            in_flight["now"] -= 1
        return _payload(eid)

    calls = _mock_get(monkeypatch, handler)

    result = vs.get_resend_email_statuses(ids)

    assert result["success"] is True
    # Same keys, same order, same values as the sequential version.
    assert list(result["statuses"].keys()) == ids
    for eid in ids:
        assert result["statuses"][eid] == {
            "id": eid,
            "to": ["vol@example.com"],
            "subject": f"Subject {eid}",
            "created_at": "2026-10-01T00:00:00Z",
            "last_event": "delivered",
        }
    assert sorted(calls) == sorted(ids)
    # The fetches overlapped instead of running one at a time.
    assert in_flight["max"] >= 3


def test_cached_ids_are_not_refetched(monkeypatch, fake_cache):
    store, _sets = fake_cache
    cached_a = {"id": "re_a", "to": [], "subject": "A", "created_at": "", "last_event": "delivered"}
    cached_c = {"id": "re_c", "to": [], "subject": "C", "created_at": "", "last_event": "opened"}
    store["resend:status:re_a"] = cached_a
    store["resend:status:re_c"] = cached_c

    calls = _mock_get(monkeypatch, lambda eid: _payload(eid))

    result = vs.get_resend_email_statuses(["re_a", "re_b", "re_c", "re_d"])

    assert result["success"] is True
    assert sorted(calls) == ["re_b", "re_d"]
    # Key order matches the old sequential version: cached entries first
    # (in input order), then freshly fetched ones (in input order).
    assert list(result["statuses"].keys()) == ["re_a", "re_c", "re_b", "re_d"]
    assert result["statuses"]["re_a"] is cached_a
    assert result["statuses"]["re_c"] is cached_c
    assert result["statuses"]["re_b"]["last_event"] == "delivered"


def test_failed_fetch_retries_once_and_returns_unknown_uncached(monkeypatch, fake_cache):
    _store, sets = fake_cache
    # Keep the 0.6s retry backoff from slowing the test down.
    monkeypatch.setattr(vs.time, "sleep", lambda _s: None)

    def handler(eid):
        if eid == "re_bad":
            raise RuntimeError("boom")
        return _payload(eid)

    calls = _mock_get(monkeypatch, handler)

    result = vs.get_resend_email_statuses(["re_good", "re_bad"])

    assert result["success"] is True
    assert calls.count("re_bad") == 2  # one retry, same as the old loop
    assert calls.count("re_good") == 1
    bad = result["statuses"]["re_bad"]
    assert bad["last_event"] == "unknown"
    assert "boom" in bad["error"]
    # Failures are not cached; successes are.
    cached_keys = [key for key, _value, _ttl in sets]
    assert "resend:status:re_good" in cached_keys
    assert "resend:status:re_bad" not in cached_keys


def test_fetched_results_cached_with_terminal_and_transient_ttls(monkeypatch, fake_cache):
    _store, sets = fake_cache
    events = {"re_done": "delivered", "re_pending": "sent"}
    _mock_get(monkeypatch, lambda eid: _payload(eid, last_event=events[eid]))

    result = vs.get_resend_email_statuses(["re_done", "re_pending"])

    assert result["success"] is True
    ttls = {key: ttl for key, _value, ttl in sets}
    assert ttls["resend:status:re_done"] == vs._RESEND_STATUS_TERMINAL_TTL
    assert ttls["resend:status:re_pending"] == vs._RESEND_STATUS_TRANSIENT_TTL
