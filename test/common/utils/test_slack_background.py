"""send_slack_audit / async_send_slack run on a bounded background pool.

- The caller gets its thread back immediately. The old audit was a synchronous
  ``requests.post`` with NO timeout, so every one of its ~70 call sites (profile
  saves, helping toggles, mentor writes, team edits...) held a gunicorn thread
  until Slack answered or TCP gave up.
- The POST carries a timeout and every requests error is swallowed inside the
  background job, so a Slack outage never surfaces in business logic.
- The payload is snapshotted at call time and the caller's dict is never mutated.
"""
import os
import threading

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import pytest
import requests

import common.utils.slack as slack

WEBHOOK = "https://hooks.slack.test/services/T000/B000/XXX"


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setattr(slack, "SLACK_URL", WEBHOOK)


def test_audit_posts_in_background_with_timeout(webhook):
    with patch.object(slack.requests, "post") as post:
        fut = slack.send_slack_audit(
            action="helping", message="user-1", payload={"x": 1, "recaptchaToken": "secret"}
        )
        assert fut is not None
        assert fut.result(timeout=5) is None
    post.assert_called_once()
    kwargs = post.call_args.kwargs
    assert kwargs["url"] == WEBHOOK
    assert kwargs["timeout"] == slack.SLACK_HTTP_TIMEOUT_SECONDS
    text = kwargs["json"]["text"]
    assert text.startswith("[helping] user-1\n")
    assert "'x': 1" in text
    assert "recaptchaToken" not in text and "secret" not in text


def test_audit_without_payload_posts_bare_line(webhook):
    with patch.object(slack.requests, "post") as post:
        slack.send_slack_audit(action="edit_team", message="Editing").result(timeout=5)
    assert post.call_args.kwargs["json"] == {"text": "[edit_team] Editing"}


def test_audit_returns_before_the_post_finishes(webhook):
    started = threading.Event()
    release = threading.Event()

    def slow_post(**kwargs):
        started.set()
        release.wait(timeout=5)

    with patch.object(slack.requests, "post", side_effect=slow_post):
        fut = slack.send_slack_audit(action="a", message="m")
        assert started.wait(timeout=5)
        # The caller already has control while the POST is still in flight.
        assert not fut.done()
        release.set()
        fut.result(timeout=5)


def test_audit_snapshots_payload_and_never_mutates_it(webhook):
    release = threading.Event()

    def slow_post(**kwargs):
        release.wait(timeout=5)

    payload = {"status": "helping", "recaptchaToken": "secret"}
    with patch.object(slack.requests, "post", side_effect=slow_post) as post:
        fut = slack.send_slack_audit(action="a", message="m", payload=payload)
        payload["status"] = "mutated-after-call"
        release.set()
        fut.result(timeout=5)
    text = post.call_args.kwargs["json"]["text"]
    assert "'status': 'helping'" in text
    assert "mutated-after-call" not in text
    # We copied: the caller's dict still has its token and its later edit.
    assert payload == {"status": "mutated-after-call", "recaptchaToken": "secret"}


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.Timeout("slow"),
        requests.exceptions.MissingSchema("CHANGEMEPLS"),
        RuntimeError("anything else"),
    ],
)
def test_audit_swallows_errors_in_the_background(webhook, exc):
    with patch.object(slack.requests, "post", side_effect=exc):
        fut = slack.send_slack_audit(action="a", message="m")
        assert fut.result(timeout=5) is None
        assert fut.exception() is None


@pytest.mark.parametrize("url", ["", None, "CHANGEMEPLS"])
def test_audit_skips_when_webhook_unset(monkeypatch, url):
    monkeypatch.setattr(slack, "SLACK_URL", url)
    with patch.object(slack.requests, "post") as post:
        assert slack.send_slack_audit(action="a", message="m", payload={"k": "v"}) is None
    post.assert_not_called()


def test_async_send_slack_runs_send_slack_off_thread():
    seen = {}

    def fake_send_slack(**kwargs):
        seen["thread"] = threading.current_thread().name
        seen["kwargs"] = kwargs

    with patch.object(slack, "send_slack", side_effect=fake_send_slack):
        fut = slack.async_send_slack(message="hi", channel="npo-foo", icon_emoji=":tada:")
        fut.result(timeout=5)

    assert seen["kwargs"] == {
        "message": "hi",
        "channel": "npo-foo",
        "icon_emoji": ":tada:",
        "username": "Hackathon Bot",
        "blocks": None,
    }
    assert seen["thread"].startswith("slack-bg")
    assert seen["thread"] != threading.current_thread().name


def test_async_send_slack_swallows_errors():
    with patch.object(slack, "send_slack", side_effect=RuntimeError("channel_not_found")):
        fut = slack.async_send_slack(message="hi", channel="nope")
        assert fut.result(timeout=5) is None
        assert fut.exception() is None


def test_background_pool_is_bounded_and_shared():
    ex1 = slack._background_executor_for_process()
    ex2 = slack._background_executor_for_process()
    assert ex1 is ex2
    assert ex1._max_workers == slack._BACKGROUND_MAX_WORKERS


def test_run_in_background_falls_back_inline_when_pool_is_shut_down():
    calls = []
    with patch.object(slack, "_background_executor_for_process") as pool:
        pool.return_value.submit.side_effect = RuntimeError(
            "cannot schedule new futures after interpreter shutdown"
        )
        assert slack.run_in_background("x", calls.append, 1) is None
    assert calls == [1]
