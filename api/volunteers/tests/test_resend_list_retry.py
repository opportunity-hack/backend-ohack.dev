"""Tests for _fetch_and_cache_resend_emails retry policy.

Sentry (backend-ohack-prod, issue 7778044789):
  "Error fetching Resend emails page after retries [page=1]"

The admin email list fetch hand-rolled its own retries: it retried EVERY
exception (including permanent errors) and then logged at error level, so a
Resend rate limit -- a "slow down" signal, not a failure -- paged Sentry.
The send path already had the right policy in services.resend_utils
(send_with_retry: retry only RateLimitError, backoff, warning level).

The list fetch now uses send_with_retry too:
- rate limit, then success -> the page data is returned
- rate limit on every attempt -> warning level only, no ERROR record,
  function keeps its contract and returns None when no page was fetched
- any other error -> NOT retried, still logged at error level
"""
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

# Allow running this file copied outside the repo tree (pytest inserts the
# test file's own directory into sys.path, not the repo root).
for _candidate in (
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    os.getcwd(),
):
    if os.path.isdir(os.path.join(_candidate, "services")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import pytest
from resend.exceptions import RateLimitError

import services.volunteers_service as vs
from services.resend_utils import MAX_ATTEMPTS


def _rate_limit_error():
    return RateLimitError(
        "Too many requests. You can only make 10 requests per second.",
        "rate_limit_exceeded",
        429,
    )


def _email_item(days_ago):
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": "re_test_1",
        "to": ["volunteer@example.com"],
        "subject": "Welcome",
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_event": "delivered",
    }


@pytest.fixture
def fetch_env(monkeypatch):
    """Wire _fetch_and_cache_resend_emails to a fake resend.Emails.list.

    Returns the list of params dicts the fake was called with.
    """
    calls = []
    state = {"handler": None}

    def fake_list(params):
        calls.append(params)
        return state["handler"](params)

    fake_resend = types.SimpleNamespace(
        api_key=None,
        Emails=types.SimpleNamespace(list=fake_list),
    )
    monkeypatch.setattr(vs, "resend", fake_resend)
    monkeypatch.setenv("RESEND_EMAIL_STATUS_KEY", "test-key")
    # No real sleeping: neither the 0.5s page delay in volunteers_service nor
    # the 1s/2s backoff inside send_with_retry.
    monkeypatch.setattr("services.volunteers_service.time.sleep", lambda _s: None)
    monkeypatch.setattr("services.resend_utils.time.sleep", lambda _s: None)
    # Cache writes go nowhere; the refresh lock release stays observable.
    monkeypatch.setattr(vs, "set_cached", MagicMock())
    monkeypatch.setattr(vs, "delete_cached", MagicMock())
    return calls, state


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_rate_limit_then_success_returns_data(fetch_env, caplog):
    calls, state = fetch_env
    attempts = []

    def flaky(_params):
        attempts.append(1)
        if len(attempts) == 1:
            raise _rate_limit_error()
        # One page, older than the 90-day cutoff, so pagination stops here.
        return {"data": [_email_item(days_ago=200)], "has_more": True}

    state["handler"] = flaky

    with caplog.at_level(logging.DEBUG):
        result = vs._fetch_and_cache_resend_emails()

    assert result is not None
    assert result["total_fetched"] == 1
    assert len(calls) == 2  # one rate-limited attempt, one success
    assert _error_records(caplog) == []


def test_rate_limit_exhaustion_warns_and_returns_none(fetch_env, caplog):
    calls, state = fetch_env

    def always_limited(_params):
        raise _rate_limit_error()

    state["handler"] = always_limited

    with caplog.at_level(logging.DEBUG):
        result = vs._fetch_and_cache_resend_emails()

    # Current contract: no page fetched and no partial data -> None.
    assert result is None
    assert len(calls) == MAX_ATTEMPTS
    # The whole point of the fix: a persistent rate limit is a warning,
    # never an error, so Sentry stays quiet (issue 7778044789).
    assert _error_records(caplog) == [], (
        f"rate limit produced ERROR-level logs: "
        f"{[r.getMessage() for r in _error_records(caplog)]}"
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a warning when the rate limit outlasts retries"
    # The refresh lock is released so the next request can retry.
    vs.delete_cached.assert_called_with(vs._RESEND_LOCK_KEY)


def test_other_errors_are_not_retried(fetch_env, caplog):
    calls, state = fetch_env

    def broken(_params):
        raise ValueError("invalid list params")

    state["handler"] = broken

    with caplog.at_level(logging.DEBUG):
        result = vs._fetch_and_cache_resend_emails()

    assert result is None
    assert len(calls) == 1  # no retry for a permanent error
    # Genuine failures still log at error level.
    assert _error_records(caplog), "expected an ERROR log for a real failure"
