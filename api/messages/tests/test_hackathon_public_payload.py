"""
get_single_hackathon_event backs the UNAUTHENTICATED GET /api/messages/hackathon/<id>
and returns (almost) the whole hackathon doc. Anything operational we stash on
that doc must be stripped before it goes out.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import services.hackathons_service as hs


def _fresh_event():
    return {
        "id": "doc-1",
        "event_id": "event-1",
        "title": "Fall 2026",
        "deadlines": {"submission": "2026-10-10T15:00:00-07:00"},
        "reminders_sent": {
            "submission_24h": {
                "sent_at": "2026-10-09T15:07:00+00:00",
                "teams_notified": ["team-a", "team-b"],
                "by": "admin",
            }
        },
        "nonprofits": [],
        "teams": [],
    }


def test_public_event_payload_omits_reminder_bookkeeping(monkeypatch):
    hs.get_single_hackathon_event.cache_clear()
    monkeypatch.setattr(hs, "get_hackathon_by_event_id", lambda event_id: _fresh_event())

    payload = hs.get_single_hackathon_event("event-1")

    assert "reminders_sent" not in payload
    # Deadlines ARE public by design (the dashboard countdown + event page read them).
    assert payload["deadlines"] == {"submission": "2026-10-10T15:00:00-07:00"}
    assert payload["title"] == "Fall 2026"
    hs.get_single_hackathon_event.cache_clear()


def test_public_event_list_omits_reminder_bookkeeping():
    from unittest.mock import MagicMock

    snap = MagicMock(spec=hs.DocumentSnapshot)
    snap.id = "doc-list-1"
    snap.to_dict.return_value = {
        "event_id": "event-1",
        "title": "Fall 2026",
        "reminders_sent": {"submission_1h": {"by": "admin", "teams_notified": ["team-a"]}},
    }

    [event] = hs._process_hackathon_docs([snap])

    assert "reminders_sent" not in event
    assert event["title"] == "Fall 2026"
