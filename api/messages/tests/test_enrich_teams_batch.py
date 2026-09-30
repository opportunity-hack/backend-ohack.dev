"""_enrich_teams_users_batch must not wipe teams whose users[] are already
enriched dicts (hardening 3.1 — mixed input used to zero their members)."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import MagicMock

from services.hackathons_service import _enrich_teams_users_batch


def _snap(uid, name):
    s = MagicMock()
    s.id = uid
    s.exists = True
    s.to_dict.return_value = {"user_id": f"oauth|{uid}", "name": name}
    return s


def test_already_enriched_team_keeps_members_when_mixed_with_raw_team():
    enriched_members = [{"id": "u1", "user_id": "oauth|u1", "name": "A", "nickname": None, "profile_image": None}]
    enriched = {"id": "t1", "users": list(enriched_members)}
    raw = {"id": "t2", "users": ["u2"]}
    db = MagicMock()
    db.get_all.return_value = [_snap("u2", "B")]

    result = _enrich_teams_users_batch([enriched, raw], db)

    assert result[0]["users"] == enriched_members
    assert result[1]["users"][0]["id"] == "u2"
    assert result[1]["users"][0]["name"] == "B"


def test_mixed_users_within_one_team_keep_dicts_and_enrich_ids():
    team = {"id": "t1", "users": [{"id": "u1", "name": "A"}, "u2"]}
    db = MagicMock()
    db.get_all.return_value = [_snap("u2", "B")]

    result = _enrich_teams_users_batch([team], db)

    assert [u["id"] for u in result[0]["users"]] == ["u1", "u2"]
    assert result[0]["users"][0]["name"] == "A"
