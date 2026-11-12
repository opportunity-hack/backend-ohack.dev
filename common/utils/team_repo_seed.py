"""Files seeded into every team's GitHub repo when an organizer approves the
team (``approve_team`` → ``create_github_repo(seed_files=...)``).

The repo is the one surface every team touches no matter which AI tool
they use, so this is where the event-day nudges live:

- ``README.md``       — real nonprofit / problem statement / Slack / dashboard
                        links, the final-day timeline, the submission checklist.
- ``AGENTS.md``       — context + working agreements read by Cursor, Cline,
                        Continue, Codex and Claude Code (``CLAUDE.md`` just
                        imports it): small PRs, short titles, descriptions
                        under 500 words, the demo-video deadline, the rubric.
- ``.claude/``        — two Claude Code hooks: a session-start clock that says
                        how long until demo-video time / close, and a guard
                        that blocks ``git commit``/``push`` of API keys or
                        ``.env`` files. No skills (Greg's call, Nov 2026).
- ``.github/``        — PR template + a comment-only PR lint workflow.
- ``.gitignore``, ``.env.example``, a real MIT ``LICENSE``.

Everything here is pure (no Firestore, no GitHub): ``build_seed_files``
takes a ``SeedContext`` and returns ``[{path, message, content}]``. The
caller builds the context from the hackathon / team / nonprofit docs it
already has. Times are derived from the hackathon doc (``deadlines.submission``
→ a "Hacking ends" countdown → end of ``end_date``) so the same code is right
for the next event; the demo-video nudge is ``DEMO_VIDEO_LEAD_HOURS`` before
the close.

Templates live in ``team_repo_templates/`` and use ``{{NAME}}`` placeholders
(not ``str.format`` — the shell/JSON/YAML templates are full of braces).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("common.utils.team_repo_seed")

TEMPLATE_DIR = Path(__file__).parent / "team_repo_templates"

DEMO_VIDEO_LEAD_HOURS = 2          # start recording this long before hacking ends
DEMO_VIDEO_MAX_MINUTES = 4         # matches the long-standing "4 minutes or less"
PR_TITLE_MAX_CHARS = 72
PR_BODY_MAX_WORDS = 500
PR_MAX_CHANGED_LINES = 400
DEFAULT_TIMEZONE = "America/Phoenix"
ASK_A_MENTOR_CHANNEL_ID = "C01E5CGDQ74"

_CLOSE_COUNTDOWN_RE = re.compile(r"hack(ing)?\s+ends?|submissions?\s+(close|due|deadline)", re.I)
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


@dataclass
class SeedContext:
    event_id: str
    event_title: str = ""
    hackathon: dict = field(default_factory=dict)   # the raw hackathon doc
    team_id: str = ""
    team_name: str = ""
    team_slack_channel: str = ""
    creator_name: str = ""
    nonprofit_id: str = ""
    nonprofit_name: str = ""
    nonprofit_slack_channel: str = ""
    problem_statement_id: str = ""
    problem_statement_title: str = ""
    problem_statement_slack_channel: str = ""
    devpost_url: str = ""
    github_org: str = ""
    repo_name: str = ""


# ---------------------------------------------------------------------------
# Time derivation
# ---------------------------------------------------------------------------

def _tz(name):
    """zoneinfo first (stdlib), pytz as the fallback for images without tzdata."""
    name = name or DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError or no zoneinfo at all
        try:
            import pytz
            return pytz.timezone(name)
        except Exception:
            return timezone.utc


def _localize(naive, tz):
    """Attach `tz` to a naive datetime, for both zoneinfo and pytz zones."""
    if hasattr(tz, "localize"):
        return tz.localize(naive)
    return naive.replace(tzinfo=tz)


def parse_event_time(value, tz):
    """Parse the time shapes the hackathon doc carries: ISO with a colon or
    colon-less offset (`2026-11-15T14:00:00-0700`, as countdowns are stored),
    a trailing `Z`, or a naive wall-clock time (taken as event-local).
    Returns an aware datetime or None."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    # `-0700` → `-07:00` so fromisoformat (3.10) accepts it
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = _localize(dt, tz)
    return dt


def resolve_close(hackathon, tz):
    """The instant hacking ends, in order of trust: `deadlines.submission`
    (the Team Dashboard's real cutoff) → a countdown named like "Hacking ends"
    → end of the `end_date` day. Returns (datetime|None, source)."""
    hackathon = hackathon or {}
    deadline = (hackathon.get("deadlines") or {}).get("submission")
    dt = parse_event_time(deadline, tz)
    if dt:
        return dt, "deadline"
    for item in hackathon.get("countdowns") or []:
        if not isinstance(item, dict):
            continue
        if _CLOSE_COUNTDOWN_RE.search(item.get("name") or ""):
            dt = parse_event_time(item.get("time"), tz)
            if dt:
                return dt, "countdown"
    end_date = hackathon.get("end_date")
    if end_date:
        try:
            day = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
            return _localize(datetime.combine(day, time(23, 59)), tz), "end_date"
        except ValueError:
            pass
    return None, "none"


def final_day_schedule(hackathon, tz, close, demo_start):
    """Countdown entries that fall on the closing day, plus our synthetic
    "start recording your demo video" row, sorted. [(datetime, label)]."""
    rows = []
    if close is None:
        return rows
    for item in (hackathon or {}).get("countdowns") or []:
        if not isinstance(item, dict):
            continue
        dt = parse_event_time(item.get("time"), tz)
        if dt and dt.astimezone(tz).date() == close.astimezone(tz).date():
            rows.append((dt, (item.get("name") or "").strip() or "Milestone"))
    if demo_start is not None:
        rows.append((demo_start, "Start recording your demo video"))
    rows.sort(key=lambda r: r[0])
    return rows


def fmt_local(dt, tz):
    """'Sun Nov 15, 12:00 PM MST' — no leading zeros, platform-independent."""
    local = dt.astimezone(tz)
    hour12 = local.hour % 12 or 12
    return (f"{local.strftime('%a %b')} {local.day}, {hour12}:{local.strftime('%M %p')} "
            f"{local.strftime('%Z') or 'local'}")


def derive_schedule(hackathon):
    """Everything the templates need about time, or mostly-empty strings when
    the doc has no usable dates (the templates degrade to 'see the event page')."""
    tz = _tz((hackathon or {}).get("timezone"))
    close, source = resolve_close(hackathon, tz)
    demo_start = close - timedelta(hours=DEMO_VIDEO_LEAD_HOURS) if close else None
    rows = final_day_schedule(hackathon, tz, close, demo_start)
    return {
        "tz": tz,
        "tz_name": getattr(tz, "key", None) or getattr(tz, "zone", None) or DEFAULT_TIMEZONE,
        "close": close,
        "close_source": source,
        "demo_start": demo_start,
        "close_local": fmt_local(close, tz) if close else "",
        "demo_local": fmt_local(demo_start, tz) if demo_start else "",
        "close_epoch": int(close.timestamp()) if close else 0,
        "demo_epoch": int(demo_start.timestamp()) if demo_start else 0,
        "final_day_rows": [(fmt_local(dt, tz), label) for dt, label in rows],
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _slack_link(channel):
    channel = (channel or "").lstrip("#").strip()
    if not channel:
        return ""
    return f"[#{channel}](https://opportunity-hack.slack.com/app_redirect?channel={channel})"


def _render(template_name, values):
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")

    def sub(match):
        key = match.group(1)
        if key not in values:
            raise KeyError(f"{template_name}: no value for {{{{{key}}}}}")
        return str(values[key])

    return _PLACEHOLDER_RE.sub(sub, text)


def _timeline_markdown(schedule):
    rows = schedule["final_day_rows"]
    if not rows:
        return ("_Final-day times aren't in the event record yet — follow the schedule on the "
                "event page and listen for announcements._")
    out = []
    for when, label in rows:
        bold = label.lower().startswith("start recording") or _CLOSE_COUNTDOWN_RE.search(label)
        out.append(f"- **{when} — {label}**" if bold else f"- {when} — {label}")
    return "\n".join(out)


def _timeline_plain(schedule):
    rows = schedule["final_day_rows"]
    if not rows:
        return "- Final-day times are on the event page; check it Sunday morning."
    return "\n".join(f"- {when} — {label}" for when, label in rows)


def template_values(ctx):
    """The {{PLACEHOLDER}} map shared by every template."""
    schedule = derive_schedule(ctx.hackathon)
    site = "https://www.ohack.dev"
    event_url = f"{site}/hack/{ctx.event_id}"
    team_url = f"{event_url}/team/{ctx.team_id}" if ctx.team_id else event_url
    dashboard_url = f"{event_url}/manageteam"
    nonprofit_url = f"{site}/nonprofit/{ctx.nonprofit_id}" if ctx.nonprofit_id else ""
    ps_url = f"{site}/project/{ctx.problem_statement_id}" if ctx.problem_statement_id else ""
    nonprofit_channel = ctx.problem_statement_slack_channel or ctx.nonprofit_slack_channel

    if schedule["demo_local"]:
        demo_sentence = (f"Start recording your demo video by **{schedule['demo_local']}** — "
                         f"hacking ends at **{schedule['close_local']}**, and a demo you "
                         f"recorded with time to spare beats a feature you finished at 1:59.")
        demo_rule = (f"Demo video time is {schedule['demo_local']}; hacking ends "
                     f"{schedule['close_local']}. After demo time, steer toward finishing, "
                     f"recording and submitting — not new features.")
    else:
        demo_sentence = ("Start your demo video about two hours before hacking ends — a demo "
                         "you recorded with time to spare beats a feature you finished at the buzzer.")
        demo_rule = ("Start the demo video about two hours before hacking ends; after that, "
                     "steer toward finishing, recording and submitting — not new features.")

    nonprofit_line = f"[{ctx.nonprofit_name}]({nonprofit_url})" if nonprofit_url else (ctx.nonprofit_name or "your nonprofit")
    ps_line = f"[{ctx.problem_statement_title}]({ps_url})" if ps_url and ctx.problem_statement_title else (ctx.problem_statement_title or "see the nonprofit page")
    devpost_line = (f"- Optional: a [DevPost entry]({ctx.devpost_url}) — the dashboard is the submission of record."
                    if ctx.devpost_url else
                    "- DevPost is not required this event — the dashboard is the submission of record.")

    return {
        "EVENT_ID": ctx.event_id,
        "EVENT_TITLE": ctx.event_title or f"Opportunity Hack {ctx.event_id}",
        "EVENT_URL": event_url,
        "TEAM_NAME": ctx.team_name or "Your team",
        "TEAM_URL": team_url,
        "DASHBOARD_URL": dashboard_url,
        "CREATOR_NAME": ctx.creator_name or "your team creator",
        "TEAM_SLACK": _slack_link(ctx.team_slack_channel) or "your team channel",
        "NONPROFIT_LINE": nonprofit_line,
        "NONPROFIT_NAME": ctx.nonprofit_name or "your nonprofit",
        "NONPROFIT_SLACK": _slack_link(nonprofit_channel) or "the nonprofit's `#npo-…` channel",
        "PROBLEM_STATEMENT_LINE": ps_line,
        "ASK_A_MENTOR_URL": f"https://opportunity-hack.slack.com/archives/{ASK_A_MENTOR_CHANNEL_ID}",
        "DEVPOST_LINE": devpost_line,
        "DEMO_SENTENCE": demo_sentence,
        "DEMO_RULE": demo_rule,
        "DEMO_LOCAL": schedule["demo_local"] or "about two hours before hacking ends",
        "CLOSE_LOCAL": schedule["close_local"] or "the end of the hackathon",
        "DEMO_EPOCH": schedule["demo_epoch"],
        "CLOSE_EPOCH": schedule["close_epoch"],
        "TZ_NAME": schedule["tz_name"],
        "TIMELINE_MD": _timeline_markdown(schedule),
        "TIMELINE_PLAIN": _timeline_plain(schedule),
        "DEMO_MAX_MINUTES": DEMO_VIDEO_MAX_MINUTES,
        "PR_TITLE_MAX": PR_TITLE_MAX_CHARS,
        "PR_BODY_MAX_WORDS": PR_BODY_MAX_WORDS,
        "PR_MAX_LINES": PR_MAX_CHANGED_LINES,
        "YEAR": datetime.now(timezone.utc).year,
        "GITHUB_ORG": ctx.github_org,
        "REPO_NAME": ctx.repo_name,
    }


# (path in repo, template file, commit message)
SEED_FILES = [
    ("LICENSE", "LICENSE.tmpl", "Add MIT License"),
    ("README.md", "README.md.tmpl", "Add README with team, nonprofit and final-day plan"),
    ("AGENTS.md", "AGENTS.md.tmpl", "Add AGENTS.md: context and working agreements for AI coding tools"),
    ("CLAUDE.md", "CLAUDE.md.tmpl", "Add CLAUDE.md (imports AGENTS.md)"),
    (".claude/settings.json", "claude-settings.json.tmpl", "Add Claude Code hooks: session clock + secret guard"),
    (".claude/hooks/session-clock.sh", "session-clock.sh.tmpl", "Add session clock hook"),
    (".claude/hooks/guard-secrets.sh", "guard-secrets.sh.tmpl", "Add secret guard hook"),
    (".github/pull_request_template.md", "pull_request_template.md.tmpl", "Add PR template"),
    (".github/workflows/pr-lint.yml", "pr-lint.yml.tmpl", "Add comment-only PR lint"),
    (".gitignore", "gitignore.tmpl", "Add .gitignore"),
    (".env.example", "env.example.tmpl", "Add .env.example"),
]


def build_seed_files(ctx):
    """Render every seed file. Returns [{"path", "message", "content"}];
    raises on a template/placeholder mistake (callers fall back to the
    legacy README — see approve_team)."""
    values = template_values(ctx)
    files = []
    for path, template_name, message in SEED_FILES:
        content = _render(template_name, values)
        if path.endswith(".json"):
            json.loads(content)  # a broken settings.json would disable Claude Code hooks silently
        files.append({"path": path, "message": message, "content": content})
    return files
