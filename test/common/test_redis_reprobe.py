"""
A runtime Redis failure must not switch Redis off forever in one process —
that left one gunicorn worker on Redis and another on its local copy (two
cache versions). After a failure the module falls back to local and
re-probes at most every REDIS_REPROBE_SECONDS.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

from common.utils import redis_cache as rc


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise ConnectionError("redis down")

    def get(self, key):
        self._check()
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self._check()
        self.store[key] = value

    def delete(self, *keys):
        self._check()
        for k in keys:
            self.store.pop(k, None)

    def keys(self, pattern):
        self._check()
        return []


@pytest.fixture
def redis_env(monkeypatch):
    fake = FakeRedis()
    clock = {"now": 1000.0}
    probes = []

    def connect(url):
        probes.append(url)
        fake._check()
        return fake

    monkeypatch.setattr(rc.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rc, "_REDIS_URL", "redis://example:6379")
    monkeypatch.setattr(rc, "_REDIS_IMPORTABLE", True)
    monkeypatch.setattr(rc, "_connect", connect)
    monkeypatch.setattr(rc, "REDIS_ENABLED", True)
    monkeypatch.setattr(rc, "REDIS_CLIENT", fake)
    monkeypatch.setattr(rc, "_REDIS_RETRY_AT", None)
    rc.local_cache.clear()
    yield fake, clock, probes
    rc.local_cache.clear()


def test_failure_falls_back_then_reprobes(redis_env):
    fake, clock, probes = redis_env
    fake.fail = True
    assert rc.set_cached("k", 1, ttl=60) is True  # served by local fallback
    assert rc.REDIS_ENABLED is False
    assert rc.get_cached("k") == 1

    # Still down, and the re-probe isn't due yet: no probe at all.
    clock["now"] += rc.REDIS_REPROBE_SECONDS - 1
    rc.get_cached("k")
    assert probes == []

    # Due, but Redis still down: one probe, stays local, schedules the next.
    clock["now"] += 2
    rc.get_cached("k")
    assert len(probes) == 1 and rc.REDIS_ENABLED is False

    # Redis is back: the next due probe re-enables it.
    fake.fail = False
    clock["now"] += rc.REDIS_REPROBE_SECONDS + 1
    rc.set_cached("k2", {"a": 1}, ttl=60)
    assert rc.REDIS_ENABLED is True
    assert "k2" in fake.store


def test_no_reprobe_without_redis_url(redis_env, monkeypatch):
    fake, clock, probes = redis_env
    monkeypatch.setattr(rc, "_REDIS_URL", None)
    monkeypatch.setattr(rc, "_REDIS_IMPORTABLE", False)
    fake.fail = True
    rc.get_cached("k")
    clock["now"] += 10 * rc.REDIS_REPROBE_SECONDS
    rc.get_cached("k")
    assert probes == [] and rc.REDIS_ENABLED is False


def test_redis_cached_exposes_prefix_and_ttl():
    @rc.redis_cached(prefix="t:reg", ttl=7)
    def f():
        return 1

    assert f.cache_prefix == "t:reg" and f.cache_ttl == 7
