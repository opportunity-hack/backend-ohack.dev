"""
Regression tests for the KeyError: 'teams' crash in team creation.

Hackathon docs created through the admin UI can lack a ``teams`` key
entirely. queue_team inserted the team doc, then did
``event_collection_dict["teams"]`` and blew up, leaving the new team
orphaned from its event (seen on test.ohack.dev with event fall-2026,
Sep 2026). The linking now goes through _append_team_to_hackathon, which
tolerates a missing key / missing doc and is idempotent.
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import api.teams.teams_service as svc


def _db_with_hackathon(existing):
    snapshot = MagicMock()
    snapshot.to_dict.return_value = existing
    event_ref = MagicMock()
    event_ref.get.return_value = snapshot
    collection = MagicMock()
    collection.document.return_value = event_ref
    db = MagicMock()
    db.collection.return_value = collection
    return db, event_ref


def _ref(doc_id):
    r = MagicMock()
    r.id = doc_id
    return r


def test_links_team_when_hackathon_has_no_teams_key():
    db, event_ref = _db_with_hackathon({"event_id": "fall-2026", "title": "ASU Fall"})
    team = _ref("team-1")

    result = svc._append_team_to_hackathon(db, "hack-doc", team)

    assert result == [team]
    written, kwargs = event_ref.set.call_args
    assert written[0] == {"teams": [team]}
    assert kwargs.get("merge") is True


def test_appends_to_existing_teams_list():
    existing_team = _ref("team-0")
    db, event_ref = _db_with_hackathon({"teams": [existing_team]})
    team = _ref("team-1")

    result = svc._append_team_to_hackathon(db, "hack-doc", team)

    assert result == [existing_team, team]
    assert event_ref.set.call_args[0][0]["teams"] == [existing_team, team]


def test_does_not_duplicate_an_already_linked_team():
    team = _ref("team-1")
    db, event_ref = _db_with_hackathon({"teams": [team]})

    result = svc._append_team_to_hackathon(db, "hack-doc", team)

    assert result == [team]
    event_ref.set.assert_not_called()


def test_tolerates_missing_hackathon_snapshot():
    db, event_ref = _db_with_hackathon(None)
    team = _ref("team-1")

    result = svc._append_team_to_hackathon(db, "hack-doc", team)

    assert result == [team]
    assert event_ref.set.call_args[0][0] == {"teams": [team]}
