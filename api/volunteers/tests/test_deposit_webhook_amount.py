"""
checkout.session.completed must never mark a hacker deposit `paid` when the
Stripe amount is below the event's constraints.hacker_deposit.default_amount_cents.
Missing/unreadable config fails open (today's behaviour).
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import services.volunteers_service as vs


def _session(amount_total, pi="pi_123"):
    return {
        "metadata": {"kind": "hacker_deposit", "event_id": "event-1", "hacker_email": "h@example.com"},
        "payment_intent": pi,
        "amount_total": amount_total,
    }


@pytest.fixture
def harness(monkeypatch):
    db = MagicMock()
    doc = {"user_id": "u1", "deposit_status": None}
    monkeypatch.setattr(vs, "get_db", lambda: db)
    monkeypatch.setattr(vs, "_find_hacker_by_email_and_event", lambda email, event_id: ("vol-1", dict(doc)))
    monkeypatch.setattr(vs, "_clear_volunteer_caches", lambda *a, **k: None)
    audits = []
    monkeypatch.setattr("common.utils.slack.send_slack_audit", lambda **kw: audits.append(kw))

    def set_default(cents):
        event = {} if cents is None else {"constraints": {"hacker_deposit": {"enabled": True, "default_amount_cents": cents}}}
        monkeypatch.setattr("services.hackathons_service.get_single_hackathon_event", lambda event_id: event)

    def written():
        return db.collection.return_value.document.return_value.update.call_args[0][0]

    return set_default, written, audits, doc


def test_underpaid_session_is_not_marked_paid(harness):
    set_default, written, audits, _ = harness
    set_default(2500)
    ok, _msg = vs._handle_checkout_session_completed(_session(100))
    assert ok
    update = written()
    assert update["deposit_status"] == "underpaid"
    assert update["deposit_amount_cents"] == 100
    assert update["stripe_payment_intent_id"] == "pi_123"
    assert audits


def test_underpaid_check_runs_before_idempotent_paid_shortcut(harness, monkeypatch):
    set_default, written, _audits, _ = harness
    set_default(2500)
    monkeypatch.setattr(
        vs, "_find_hacker_by_email_and_event",
        lambda email, event_id: ("vol-1", {"deposit_status": "paid", "stripe_payment_intent_id": "pi_123"}),
    )
    vs._handle_checkout_session_completed(_session(100))
    assert written()["deposit_status"] == "underpaid"


def test_missing_constraint_fails_open_to_paid(harness):
    set_default, written, _audits, _ = harness
    set_default(None)
    vs._handle_checkout_session_completed(_session(100))
    assert written()["deposit_status"] == "paid"


def test_event_lookup_error_fails_open_to_paid(harness, monkeypatch):
    _set_default, written, _audits, _ = harness

    def boom(event_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr("services.hackathons_service.get_single_hackathon_event", boom)
    vs._handle_checkout_session_completed(_session(100))
    assert written()["deposit_status"] == "paid"


def test_exact_amount_is_paid(harness):
    set_default, written, _audits, _ = harness
    set_default(2500)
    vs._handle_checkout_session_completed(_session(2500))
    assert written()["deposit_status"] == "paid"
