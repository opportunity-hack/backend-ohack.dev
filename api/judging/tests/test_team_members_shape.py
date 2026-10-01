"""Judges' team pages must resolve members when get_team returns enriched
users[] dicts (it always does now) — hardening 3.1. Data fix only; the
judging process is untouched."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import api.judging.judging_service as svc


def _fake_user(uid):
    return {"id": uid, "name": "Alice", "email_address": "a@example.com", "profile_image": "img"}


def test_get_team_details_resolves_enriched_user_dicts():
    fake_team = {"team": {"id": "team-1", "name": "T", "users": [{"id": "u1", "name": "A"}]}}
    with patch.object(svc, "get_team", return_value=fake_team), \
            patch("common.utils.firebase.get_user_by_id", side_effect=_fake_user) as m:
        result = svc.get_team_details("team-1")

    m.assert_called_once_with("u1")
    assert result["team"]["members"] == [
        {"id": "u1", "name": "Alice", "email": "a@example.com", "profile_image": "img"}
    ]


def test_get_team_details_still_resolves_legacy_id_strings():
    fake_team = {"team": {"id": "team-1", "name": "T", "users": ["u1"]}}
    with patch.object(svc, "get_team", return_value=fake_team), \
            patch("common.utils.firebase.get_user_by_id", side_effect=_fake_user):
        result = svc.get_team_details("team-1")
    assert result["team"]["members"][0]["email"] == "a@example.com"


def test_format_team_for_judge_resolves_enriched_user_dicts():
    team = {"id": "team-1", "name": "T", "users": [{"id": "u1", "name": "A"}, "u2"]}
    with patch("common.utils.firebase.get_user_by_id", side_effect=_fake_user) as m:
        result = svc.format_team_for_judge(team)
    assert [c.args[0] for c in m.call_args_list] == ["u1", "u2"]
    assert len(result["members"]) == 2
    assert result["members"][0]["email"] == "a@example.com"
