"""Own-profile read under event-day load.

The navbar reads GET /api/users/profile on every hard page load and the
profile cache is 10s, so almost every read is a cache miss. Two things used to
go wrong at kickoff:

- @limits(calls=100, period=ONE_MINUTE) is per process and counts every user
  together, so the 101st miss in a minute answered 429 to everyone on that
  worker (/profile, findteam, the manageteam prefill).
- each miss posted a Slack "went to profile" audit and wrote last_login.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")  # -> MockFirestore; no network at import

import pytest

import services.users_service as us


class _User:
    def __init__(self, propel_id):
        self.id = f"db-{propel_id}"
        self.email_address = f"{propel_id}@example.com"


@pytest.fixture
def stubbed(monkeypatch):
    calls = {"audit": [], "refresh": []}
    monkeypatch.setattr(us, "_resolve_and_ensure_user",
                        lambda propel_id: (_User(propel_id), f"oauth|{propel_id}"))
    monkeypatch.setattr(us, "get_user_profile_by_db_id", lambda db_id: {"id": db_id})
    monkeypatch.setattr(us, "build_profile_response", lambda user: dict(user))
    monkeypatch.setattr(us, "send_slack_audit",
                        lambda **kw: calls["audit"].append(kw["message"]))
    monkeypatch.setattr(us, "_refresh_login_details",
                        lambda user, propel_id: calls["refresh"].append(propel_id))
    us.get_profile_metadata.cache_clear()
    us._PROFILE_VISIT_THROTTLE.clear()
    yield calls
    us.get_profile_metadata.cache_clear()
    us._PROFILE_VISIT_THROTTLE.clear()


def test_many_users_in_a_minute_are_never_rate_limited(stubbed):
    for i in range(150):
        assert us.get_profile_metadata(f"propel-load-{i}") == {"id": f"db-propel-load-{i}"}


def test_profile_visit_side_effects_fire_once_per_user_per_window(stubbed):
    for _ in range(3):
        us.get_profile_metadata.cache_clear()  # every read a miss, as with a 10s cache
        us.get_profile_metadata("propel-repeat")
    us.get_profile_metadata("propel-other")

    assert len(stubbed["audit"]) == 2
    assert stubbed["refresh"] == ["propel-repeat", "propel-other"]
