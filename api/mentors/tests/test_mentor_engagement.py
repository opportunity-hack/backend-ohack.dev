"""Mentor engagement summary (api/mentors/mentor_engagement_service.py) — pure rules."""
from api.mentors import mentor_engagement_service as svc


def _vol(vid, user_id, email, **extra):
    return {"id": vid, "user_id": user_id, "email": email, "name": vid.title(),
            "isSelected": True, **extra}


def _team(tid, **fields):
    return {"id": tid, "name": tid.upper(), **fields}


def _signoff(pid, at="2026-11-14T18:00:00"):
    return {"history": [{"propel_id": pid, "name": "X", "at": at, "action": "checked"}],
            "checks": {pid: {"name": "X", "checked_at": at}}}


def test_checked_in_and_active_on_two_teams_is_engaged():
    vols = [_vol("ann", "p-ann", "ann@x.org",
                 checkInTimeList=["2026-11-14T09:00:00-07:00"],
                 checkoutTimeList=["2026-11-14T12:30:00-07:00"])]
    teams = [
        _team("t1", mentor_checklist={"intro_made": _signoff("p-ann"), "scope_reviewed": _signoff("p-ann")},
              mentor_notes=[{"author_propel_id": "p-ann", "created_at": "2026-11-14T19:00:00"}]),
        _team("t2", mentor_ratings=[{"rated_by_propel_id": "p-ann", "rated_at": "2026-11-14T20:00:00"}],
              mentor_flags=[{"raised_by_propel_id": "p-ann", "created_at": "2026-11-14T20:30:00",
                             "resolved_at": "2026-11-14T21:00:00", "resolved_by_propel_id": "p-ann"}]),
    ]
    row = svc.summarize_mentor_engagement(vols, teams)["mentors"][0]
    assert row["level"] == svc.LEVEL_ENGAGED
    assert (row["coverage_signoffs"], row["notes"], row["ratings"]) == (2, 1, 1)
    assert (row["flags_raised"], row["flags_resolved"]) == (1, 1)
    assert row["panel_actions"] == 6 and row["teams_helped"] == 2
    assert row["checked_in_hours"] == 3.5
    assert row["last_panel_activity_at"] == "2026-11-14T21:00:00Z"


def test_showing_up_alone_is_not_engaged():
    vols = [_vol("bob", "p-bob", "bob@x.org", checkInTimeList=["2026-11-14T09:00:00-07:00"])]
    row = svc.summarize_mentor_engagement(vols, [])["mentors"][0]
    assert row["level"] == svc.LEVEL_CHECKED_IN_ONLY
    assert row["sessions_without_checkout"] == 1 and row["checked_in_hours"] == 0


def test_panel_use_without_check_in_is_review_not_engaged():
    teams = [_team(f"t{i}", mentor_notes=[{"author_propel_id": "p-cy", "created_at": "2026-11-14T19:00:00"}] * 3)
             for i in range(2)]
    row = svc.summarize_mentor_engagement([_vol("cy", "p-cy", "cy@x.org")], teams)["mentors"][0]
    assert row["panel_actions"] == 6 and row["level"] == svc.LEVEL_SOME


def test_desk_check_in_counts_as_checked_in():
    row = svc.summarize_mentor_engagement([_vol("di", "p-di", "di@x.org", checkedIn=True)], [])["mentors"][0]
    assert row["checked_in"] and row["desk_checked_in"]


def test_activity_matched_by_email_when_doc_is_stored_under_another_id():
    vols = [_vol("eve", "oauth|slack|U1", "Eve@X.org")]
    teams = [_team("t1", mentor_notes=[{"author_propel_id": "p-eve", "created_at": "2026-11-14T19:00:00"}])]
    out = svc.summarize_mentor_engagement(vols, teams, resolve_email=lambda pid: "eve@x.org")
    assert out["mentors"][0]["notes"] == 1 and out["unmatched"] == []


def test_unmatched_activity_is_reported_not_dropped():
    def boom(_pid):
        raise RuntimeError("propelauth down")
    teams = [_team("t1", mentor_notes=[{"author_propel_id": "p-zed", "author_name": "Zed",
                                        "created_at": "2026-11-14T19:00:00"}])]
    out = svc.summarize_mentor_engagement([], teams, resolve_email=boom)
    assert out["unmatched"][0]["name"] == "Zed" and out["unmatched"][0]["notes"] == 1


def test_deleted_notes_cleared_checks_and_legacy_shapes():
    teams = [_team("t1",
                   mentor_notes=[{"author_propel_id": "p-fay", "deleted_at": "2026-11-14T19:05:00"}],
                   mentor_checklist={
                       # pre-history sign-off: only in `checks`
                       "intro_made": {"checks": {"p-fay": {"name": "Fay", "checked_at": "2026-11-14T18:00:00"}}},
                       # legacy single-mentor shape
                       "scope_reviewed": {"done": True, "checked_by_propel_id": "p-fay",
                                          "checked_at": "2026-11-14T18:10:00"},
                       # re-checked twice: both sign-offs count, the clear does not
                       "repo_health_checked": {"history": [
                           {"propel_id": "p-fay", "at": "2026-11-14T10:00:00", "action": "checked"},
                           {"propel_id": "p-fay", "at": "2026-11-14T11:00:00", "action": "cleared"},
                           {"propel_id": "p-fay", "at": "2026-11-14T16:00:00", "action": "checked"}],
                           "checks": {"p-fay": {"checked_at": "2026-11-14T16:00:00"}}},
                   })]
    row = svc.summarize_mentor_engagement([_vol("fay", "p-fay", "fay@x.org")], teams)["mentors"][0]
    assert row["notes"] == 0 and row["coverage_signoffs"] == 4


def test_unselected_applicant_with_no_activity_is_left_out():
    vols = [_vol("gus", "p-gus", "gus@x.org", isSelected=False)]
    out = svc.summarize_mentor_engagement(vols, [])
    assert out["mentors"] == [] and out["summary"]["roster"] == 0


def test_one_team_is_enough_and_rows_are_ranked_most_engaged_first():
    def notes(pid, n):
        return [{"author_propel_id": pid, "created_at": "2026-11-14T19:00:00"}] * n
    checked_in = {"checkInTimeList": ["2026-11-14T09:00:00-07:00"]}
    vols = [_vol("low", "p-low", "low@x.org", **checked_in),
            _vol("idle", "p-idle", "idle@x.org"),
            _vol("top", "p-top", "top@x.org", **checked_in),
            _vol("mid", "p-mid", "mid@x.org", **checked_in)]
    teams = [_team("t1", mentor_notes=notes("p-top", 9) + notes("p-mid", 5) + notes("p-low", 2))]
    rows = svc.summarize_mentor_engagement(vols, teams)["mentors"]
    assert [(r["rank"], r["volunteer_id"]) for r in rows] == [(1, "top"), (2, "mid"), (3, "low"), (4, "idle")]
    assert [r["level"] for r in rows] == [svc.LEVEL_ENGAGED, svc.LEVEL_ENGAGED, svc.LEVEL_SOME, svc.LEVEL_NONE]


def test_score_adds_up_and_caps_hours():
    vols = [_vol("ann", "p-ann", "ann@x.org",
                 checkInTimeList=["2026-11-14T09:00:00-07:00"],
                 checkoutTimeList=["2026-11-15T09:00:00-07:00"])]  # forgot to check out: 24h
    teams = [_team("t1", mentor_notes=[{"author_propel_id": "p-ann", "created_at": "2026-11-14T19:00:00"}] * 3)]
    row = svc.summarize_mentor_engagement(vols, teams)["mentors"][0]
    # 3 actions + 1 team x 2 + checked in 2 + hours capped at 10
    assert row["score"] == 3 + 2 + 2 + 10
    assert sum(p["points"] for p in row["score_breakdown"]) == row["score"]
    idle = svc.summarize_mentor_engagement([_vol("bob", "p-bob", "bob@x.org")], [])["mentors"][0]
    assert idle["score"] == 0
