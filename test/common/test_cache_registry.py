"""
Guard: every cache in the source tree is listed in common/utils/cache_registry.py
with its TTL and class, and every read_after_write cache stays <= 10s.

Why: gunicorn runs 2 workers per Fly machine (prod has 2 machines) and there is
no Redis, so each cache exists in up to 4 independent copies. A write clears
only one of them; the TTL is the cross-worker staleness window. Adding a cache
without classifying it is how "refresh alternates between two states" shipped.
"""
import os

from common.utils import cache_registry as cr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _found():
    return cr.scan_caches(ROOT)


def test_scanner_finds_known_shapes():
    found = _found()
    # One of each kind, so a scanner regression can't make the guard vacuous.
    assert ("services.hackathons_service", "get_single_hackathon_event", "cached") in found
    assert ("services.volunteers_service", "get_volunteer_by_user_id", "redis_cached") in found
    assert ("services.teams_service", "_TEAM_USERS_CACHE", "ttlcache") in found
    assert ("services.broadcasts_service", "list_contacts", "manual") in found


def test_every_cache_is_registered():
    registered = {c.key for c in cr.CACHES}
    missing = sorted(set(_found()) - registered)
    assert not missing, (
        "Unregistered caches — add them to common/utils/cache_registry.py CACHES "
        "with a class (static / read_after_write / coordination):\n"
        + "\n".join(f"  {m}" for m in missing)
    )


def test_no_stale_registry_entries():
    stale = sorted({c.key for c in cr.CACHES} - set(_found()))
    assert not stale, "Registry entries with no matching cache (renamed/removed?):\n" + "\n".join(
        f"  {s}" for s in stale
    )


def test_registered_ttls_match_source():
    found = _found()
    drift = [
        (c.key, c.ttl, found[c.key])
        for c in cr.CACHES
        if c.key in found and c.ttl != found[c.key]
    ]
    assert not drift, "TTL changed in code but not in the registry (key, registry, code):\n" + "\n".join(
        f"  {d}" for d in drift
    )


def test_read_after_write_ttls_are_short():
    too_long = [
        (c.key, c.ttl) for c in cr.CACHES
        if c.cls == cr.READ_AFTER_WRITE and (c.ttl is None or c.ttl > cr.READ_AFTER_WRITE_MAX_TTL)
    ]
    assert not too_long, (
        f"read_after_write caches must be <= {cr.READ_AFTER_WRITE_MAX_TTL}s "
        "(each worker holds its own copy):\n" + "\n".join(f"  {t}" for t in too_long)
    )


def test_registry_is_well_formed():
    keys = [c.key for c in cr.CACHES]
    assert len(keys) == len(set(keys)), "duplicate registry entries"
    for c in cr.CACHES:
        assert c.cls in cr.CLASSES, c
        assert c.kind in cr.KINDS, c


def test_scanner_flags_new_cache(tmp_path):
    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "mod.py").write_text(
        "from cachetools import cached, TTLCache\n"
        "TTL = 30\n"
        "_C = TTLCache(maxsize=1, ttl=TTL * 2)\n"
        "@cached(cache=TTLCache(maxsize=1, ttl=TTL))\n"
        "def f():\n    return 1\n"
        "@cached(cache=_C)\n"
        "def g():\n    return 2\n"
        "@redis_cached(prefix='p')\n"
        "def h():\n    return 3\n"
        "def k():\n    set_cached('x', 1, ttl=5)\n"
    )
    found = cr.scan_caches(str(tmp_path))
    assert found == {
        ("svc.mod", "_C", "ttlcache"): 60,
        ("svc.mod", "f", "cached"): 30,
        ("svc.mod", "g", "cached"): 60,
        ("svc.mod", "h", "redis_cached"): 600,
        ("svc.mod", "k", "manual"): 5,
    }
