"""Unit tests for common.utils.team_repo_seed — the files committed into every
team repo at approval. Pure: no Firestore, no GitHub.

Fixture mirrors the real Fall 2026 hackathon doc shape (countdowns with
colon-less offsets, empty `deadlines`, `timezone: America/Phoenix`).
"""
import json
import os
import re
from datetime import datetime, timezone

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

from common.utils import team_repo_seed as seed  # noqa: E402


FALL_2026 = {
    "event_id": "fall-2026",
    "title": "ASU Fall Hackathon 2026 | Phoenix Hackathon for Nonprofits: Nov 14-15",
    "start_date": "2026-11-14",
    "end_date": "2026-11-15",
    "timezone": "America/Phoenix",
    "deadlines": {},
    "countdowns": [
        {"name": "Kickoff", "time": "2026-11-14T10:00:00-0700"},
        {"name": "Presentation Feedback Hour", "time": "2026-11-15T11:00:00-0700"},
        {"name": "Judges Arrive", "time": "2026-11-15T12:30:00-0700"},
        {"name": "Hacking ends", "time": "2026-11-15T14:00:00-0700"},
        {"name": "Judging Round 1", "time": "2026-11-15T14:05:00-0700"},
        {"name": "Winners Announced", "time": "2026-11-15T16:30:00-0700"},
    ],
}


def _ctx(**overrides):
    base = dict(
        event_id="fall-2026",
        event_title=FALL_2026["title"],
        hackathon=FALL_2026,
        team_id="team-doc-1",
        team_name="Team Sparkle",
        team_slack_channel="team-sparkle",
        creator_name="Greg V",
        nonprofit_id="np-1",
        nonprofit_name="Coding in Color",
        nonprofit_slack_channel="npo-coding-in-color",
        problem_statement_id="ps-1",
        problem_statement_title="Volunteer scheduling portal",
        problem_statement_slack_channel="",
        devpost_url="",
        github_org="Opportunity-Hack-2026",
        repo_name="TeamSparkle-CodinginColor",
    )
    base.update(overrides)
    return seed.SeedContext(**base)


# ---------------------------------------------------------------------------
# Time derivation
# ---------------------------------------------------------------------------

def test_close_comes_from_hacking_ends_countdown_when_no_deadline():
    tz = seed._tz("America/Phoenix")
    close, source = seed.resolve_close(FALL_2026, tz)
    assert source == "countdown"
    assert close.astimezone(tz).strftime("%Y-%m-%d %H:%M") == "2026-11-15 14:00"


def test_deadline_submission_beats_countdown():
    tz = seed._tz("America/Phoenix")
    doc = {**FALL_2026, "deadlines": {"submission": "2026-11-15T13:30:00-07:00"}}
    close, source = seed.resolve_close(doc, tz)
    assert source == "deadline"
    assert close.astimezone(tz).strftime("%H:%M") == "13:30"


def test_close_falls_back_to_end_of_end_date():
    tz = seed._tz("America/Phoenix")
    close, source = seed.resolve_close({"end_date": "2026-11-15", "countdowns": []}, tz)
    assert source == "end_date"
    assert close.astimezone(tz).strftime("%Y-%m-%d %H:%M") == "2026-11-15 23:59"


def test_no_dates_gives_no_close():
    tz = seed._tz("America/Phoenix")
    assert seed.resolve_close({}, tz) == (None, "none")


def test_demo_start_is_two_hours_before_close_in_event_time():
    s = seed.derive_schedule(FALL_2026)
    assert s["demo_local"] == "Sun Nov 15, 12:00 PM MST"
    assert s["close_local"] == "Sun Nov 15, 2:00 PM MST"
    assert s["close_epoch"] - s["demo_epoch"] == 2 * 3600
    # Epochs are the real instants (MST = UTC-7): 14:00 MST == 21:00Z
    assert datetime.fromtimestamp(s["close_epoch"], timezone.utc).hour == 21


def test_final_day_schedule_lists_only_closing_day_plus_demo_row_sorted():
    s = seed.derive_schedule(FALL_2026)
    labels = [label for _, label in s["final_day_rows"]]
    assert "Kickoff" not in labels  # Saturday
    assert labels == [
        "Presentation Feedback Hour",
        "Start recording your demo video",
        "Judges Arrive",
        "Hacking ends",
        "Judging Round 1",
        "Winners Announced",
    ]


def test_parse_event_time_accepts_colonless_offset_z_and_naive():
    tz = seed._tz("America/Phoenix")
    a = seed.parse_event_time("2026-11-15T14:00:00-0700", tz)
    b = seed.parse_event_time("2026-11-15T21:00:00Z", tz)
    c = seed.parse_event_time("2026-11-15T14:00:00", tz)  # naive → event-local
    assert a == b == c
    assert seed.parse_event_time("not a date", tz) is None
    assert seed.parse_event_time(None, tz) is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_build_seed_files_renders_every_file_with_no_leftover_placeholders():
    files = seed.build_seed_files(_ctx())
    paths = [f["path"] for f in files]
    assert paths == [p for p, _, _ in seed.SEED_FILES]
    assert {"README.md", "AGENTS.md", "CLAUDE.md", ".claude/settings.json",
            ".claude/hooks/session-clock.sh", ".claude/hooks/guard-secrets.sh",
            ".github/pull_request_template.md", ".github/workflows/pr-lint.yml",
            ".gitignore", ".env.example", "LICENSE"} <= set(paths)
    for f in files:
        assert "{{" not in f["content"], f["path"]
        assert f["message"]


def test_readme_carries_real_links_channels_and_demo_time():
    readme = next(f for f in seed.build_seed_files(_ctx()) if f["path"] == "README.md")["content"]
    assert "# Team Sparkle — Coding in Color" in readme
    assert "https://www.ohack.dev/nonprofit/np-1" in readme
    assert "https://www.ohack.dev/project/ps-1" in readme
    assert "https://www.ohack.dev/hack/fall-2026/team/team-doc-1" in readme
    assert "https://www.ohack.dev/hack/fall-2026/manageteam" in readme
    assert "app_redirect?channel=npo-coding-in-color" in readme
    assert "app_redirect?channel=team-sparkle" in readme
    assert "Start recording your demo video by **Sun Nov 15, 12:00 PM MST**" in readme
    assert "hacking ends at **Sun Nov 15, 2:00 PM MST**" in readme
    assert "hackathon-judging-criteria" in readme
    assert "DevPost is not required" in readme


def test_readme_and_agents_insist_on_the_one_repository():
    files = {f["path"]: f["content"] for f in seed.build_seed_files(_ctx())}
    readme_head = files["README.md"].split("## Final day")[0]
    assert "ONE repository" in readme_head  # above the fold, before the timeline
    assert "don't create" in readme_head
    agents = files["AGENTS.md"]
    assert agents.index("The one repository rule") < agents.index("## Who this is for")
    assert "Never suggest, scaffold or" in agents
    assert "move it here" in agents


def test_problem_statement_channel_wins_over_nonprofit_channel():
    readme = next(f for f in seed.build_seed_files(
        _ctx(problem_statement_slack_channel="#npo-cic-portal")
    ) if f["path"] == "README.md")["content"]
    assert "channel=npo-cic-portal" in readme
    assert "channel=npo-coding-in-color" not in readme


def test_devpost_mentioned_only_when_event_has_a_link():
    readme = next(f for f in seed.build_seed_files(
        _ctx(devpost_url="https://ohack-2026.devpost.com")
    ) if f["path"] == "README.md")["content"]
    assert "[DevPost entry](https://ohack-2026.devpost.com)" in readme
    assert "the dashboard is the submission of record" in readme


def test_agents_md_states_pr_norms_and_demo_rule_and_claude_md_imports_it():
    files = {f["path"]: f["content"] for f in seed.build_seed_files(_ctx())}
    agents = files["AGENTS.md"]
    assert "under 500 words" in agents
    assert "under 72 characters" in agents
    assert "under 400 changed lines" in agents
    assert "Demo video time is Sun Nov 15, 12:00 PM MST" in agents
    assert "Scope" in agents and "Accessibility" in agents
    assert files["CLAUDE.md"].startswith("@AGENTS.md")


def test_claude_settings_is_valid_json_wiring_both_hooks():
    files = {f["path"]: f["content"] for f in seed.build_seed_files(_ctx())}
    settings = json.loads(files[".claude/settings.json"])
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "bash .claude/hooks/session-clock.sh"
    pre = settings["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash"
    assert pre["hooks"][0]["command"] == "bash .claude/hooks/guard-secrets.sh"


def test_session_clock_hook_has_the_real_epochs_baked_in():
    files = {f["path"]: f["content"] for f in seed.build_seed_files(_ctx())}
    clock = files[".claude/hooks/session-clock.sh"]
    s = seed.derive_schedule(FALL_2026)
    assert f"DEMO_EPOCH={s['demo_epoch']}" in clock
    assert f"CLOSE_EPOCH={s['close_epoch']}" in clock
    assert 'TZ_NAME="America/Phoenix"' in clock


def test_pr_lint_workflow_uses_the_shared_limits_and_never_fails_the_pr():
    files = {f["path"]: f["content"] for f in seed.build_seed_files(_ctx())}
    wf = files[".github/workflows/pr-lint.yml"]
    assert "title.length > 72" in wf
    assert "words > 500" in wf
    assert "changed > 400" in wf
    assert "core.setFailed" not in wf
    assert "pull-requests: write" in wf


def test_license_is_a_real_mit_license_not_a_placeholder():
    lic = next(f for f in seed.build_seed_files(_ctx()) if f["path"] == "LICENSE")["content"]
    assert "Permission is hereby granted, free of charge" in lic
    assert "Team Sparkle" in lic
    assert str(datetime.now(timezone.utc).year) in lic


def test_missing_dates_degrade_to_event_page_copy_without_none_strings():
    files = seed.build_seed_files(_ctx(hackathon={"event_id": "x", "timezone": "America/Phoenix"}))
    joined = "\n".join(f["content"] for f in files)
    assert "None" not in joined.replace("NonProfit", "")
    readme = next(f for f in files if f["path"] == "README.md")["content"]
    assert "about two hours before hacking ends" in readme
    assert "Final-day times aren't in the event record yet" in readme
    clock = next(f for f in files if f["path"] == ".claude/hooks/session-clock.sh")["content"]
    assert "CLOSE_EPOCH=0" in clock  # the hook prints the event-page fallback line


def test_missing_optional_context_renders_gracefully():
    files = seed.build_seed_files(seed.SeedContext(event_id="fall-2026", hackathon=FALL_2026))
    readme = next(f for f in files if f["path"] == "README.md")["content"]
    assert "Your team — your nonprofit" in readme
    assert "{{" not in readme
    assert "the nonprofit's `#npo-…` channel" in readme


def test_unknown_placeholder_in_a_template_is_a_loud_error(monkeypatch, tmp_path):
    bad = tmp_path / "bad.tmpl"
    bad.write_text("hello {{NOT_A_THING}}")
    monkeypatch.setattr(seed, "TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(seed, "SEED_FILES", [("bad.md", "bad.tmpl", "x")])
    with pytest.raises(KeyError, match="NOT_A_THING"):
        seed.build_seed_files(_ctx())
