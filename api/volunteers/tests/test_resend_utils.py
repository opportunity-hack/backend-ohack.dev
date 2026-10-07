"""Tests for services.resend_utils.send_with_retry.

Sentry (Oct 2026 weekly report, backend-ohack-prod, 15 events):
  RateLimitError: Too many requests. You can only make 10 requests per second.

Every Resend send in the codebase either let this bubble up or logged it with
logger.error, paging Sentry for what is really "the mail provider asked us to
slow down". send_with_retry retries with backoff so the mail goes out, and
logs at warning level so Sentry stays quiet.
"""
import logging

import pytest
from resend.exceptions import RateLimitError

from services.resend_utils import send_with_retry, MAX_ATTEMPTS


def _rate_limit_error():
    return RateLimitError(
        "Too many requests. You can only make 10 requests per second.",
        "rate_limit_exceeded",
        429,
    )


def test_succeeds_first_try_without_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("services.resend_utils.time.sleep", sleeps.append)

    result, error = send_with_retry(lambda: {"id": "re_123"})

    assert result == {"id": "re_123"}
    assert error is None
    assert sleeps == []


def test_retries_on_rate_limit_then_succeeds(monkeypatch, caplog):
    sleeps = []
    monkeypatch.setattr("services.resend_utils.time.sleep", sleeps.append)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _rate_limit_error()
        return {"id": "re_123"}

    with caplog.at_level(logging.WARNING, logger="services.resend_utils"):
        result, error = send_with_retry(flaky)

    assert result == {"id": "re_123"}
    assert error is None
    assert len(calls) == 3
    assert sleeps == [1, 2]  # exponential backoff: 1s, then 2s
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_returns_error_after_max_attempts(monkeypatch, caplog):
    sleeps = []
    monkeypatch.setattr("services.resend_utils.time.sleep", sleeps.append)
    calls = []

    def always_limited():
        calls.append(1)
        raise _rate_limit_error()

    with caplog.at_level(logging.WARNING, logger="services.resend_utils"):
        result, error = send_with_retry(always_limited)

    assert result is None
    assert "Too many requests" in error
    assert len(calls) == MAX_ATTEMPTS
    # Rate limits are warnings, never errors: Sentry must not page us.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_other_exceptions_are_not_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr("services.resend_utils.time.sleep", sleeps.append)
    calls = []

    def broken():
        calls.append(1)
        raise ValueError("bad params")

    with pytest.raises(ValueError, match="bad params"):
        send_with_retry(broken)

    assert len(calls) == 1
    assert sleeps == []
