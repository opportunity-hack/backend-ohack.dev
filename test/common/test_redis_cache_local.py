"""
Local (no-Redis) fallback cache: per-call ttl is honoured and glob patterns
clear. Before Nov 2026 a `@redis_cached(ttl=2)` result was held for 10 minutes
per gunicorn worker, so two workers served two versions of a volunteer doc and
/mentor-checkin alternated between "awaiting confirmation" and "not selected".
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

from common.utils import redis_cache as rc


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.setattr(rc, "REDIS_ENABLED", False)
    monkeypatch.setattr(rc, "REDIS_CLIENT", None)
    rc.local_cache.clear()
    clock = {"now": 1000.0}
    monkeypatch.setattr(rc.time, "monotonic", lambda: clock["now"])
    yield clock
    rc.local_cache.clear()


def test_per_call_ttl_is_honoured(local_only):
    rc.set_cached("k", {"status": "denied"}, ttl=2)
    assert rc.get_cached("k") == {"status": "denied"}
    local_only["now"] += 1.9
    assert rc.get_cached("k") == {"status": "denied"}
    local_only["now"] += 0.2
    assert rc.get_cached("k") is None
    assert rc.get_cached("k", "dflt") == "dflt"


def test_redis_cached_decorator_refetches_after_ttl(local_only):
    calls = []

    @rc.redis_cached(prefix="t:vol", ttl=2)
    def lookup(uid):
        calls.append(uid)
        return {"uid": uid, "n": len(calls)}

    assert lookup("u1")["n"] == 1
    assert lookup("u1")["n"] == 1  # cached
    local_only["now"] += 3
    assert lookup("u1")["n"] == 2  # expired -> fresh read


def test_clear_pattern_matches_globs(local_only):
    rc.set_cached("volunteer:by_event:fall-2026:mentor:1", [1], ttl=60)
    rc.set_cached("volunteer:by_event:fall-2026:mentor:2", [2], ttl=60)
    rc.set_cached("volunteer:by_event:fall-2026:judge:1", [3], ttl=60)
    rc.clear_pattern("volunteer:by_event:fall-2026:mentor*")
    assert rc.get_cached("volunteer:by_event:fall-2026:mentor:1") is None
    assert rc.get_cached("volunteer:by_event:fall-2026:mentor:2") is None
    assert rc.get_cached("volunteer:by_event:fall-2026:judge:1") == [3]


def test_delete_cached_and_falsy_values(local_only):
    rc.set_cached("zero", 0, ttl=60)
    assert rc.get_cached("zero") == 0
    rc.delete_cached("zero")
    assert rc.get_cached("zero") is None
