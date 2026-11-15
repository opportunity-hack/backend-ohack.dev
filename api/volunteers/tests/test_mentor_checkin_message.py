"""
#ask-a-mentor check-in announcement (Nov 2026): four lines, no blank lines,
no link previews. The old message was ~12 lines plus two LinkedIn/profile
unfurls that doubled its height.
"""
import os
from unittest.mock import patch

os.environ.setdefault("ENVIRONMENT", "test")

from services import volunteers_service as vs

MENTION = "<@U123>"

FULL = {
    "email": "mentor@example.com",
    "expertise": ["Software Engineering", "Cloud Architecture", "DevOps"],
    "softwareEngineeringSpecifics": [
        "Front-end (CSS/JS, Node, Angular, React, etc)",
        "Back-end (Java, Python, Ruby, etc.)",
        "AWS",
    ],
    "linkedinProfile": "https://www.linkedin.com/in/someone/",
    "inPerson": "No, I'll be virtual",
}


def build(volunteer, time_slot="Custom time slot", profile_url="https://www.ohack.dev/profile/abc"):
    return vs._build_mentor_checkin_message(volunteer, MENTION, time_slot=time_slot, profile_url=profile_url)


def test_full_mentor_is_four_compact_lines():
    lines = build(FULL).split("\n")
    assert lines == [
        f":reversecongaparrot: *Mentor available:* {MENTION} just checked in · 💻 Virtual",
        "*Can help with:* Software Engineering, Cloud Architecture, DevOps",
        "*Stack:* Front-end (CSS/JS, Node, Angular, React), Back-end (Java, Python, Ruby), AWS",
        "<https://www.linkedin.com/in/someone/|LinkedIn> · <https://www.ohack.dev/profile/abc|OHack profile>"
        f" · Need help? Reply in thread or DM {MENTION}",
    ]


def test_in_person_answers():
    assert "📍 In person" in build({**FULL, "inPerson": "Yes!"})
    assert "📍 In person" in build({**FULL, "inPerson": None, "isInPerson": True})
    msg = build({**FULL, "inPerson": None})
    assert "In person" not in msg and "Virtual" not in msg


def test_real_time_slot_is_kept_generic_one_dropped():
    assert "🕒" not in build(FULL)
    assert "· 🕒 Saturday Oct 11 (9am - 12pm PST)" in build(FULL, time_slot="Saturday Oct 11 (9am - 12pm PST)")


def test_empty_fields_are_skipped():
    msg = build({"email": "x@example.com"}, time_slot=None, profile_url=None)
    assert msg.split("\n") == [
        f":reversecongaparrot: *Mentor available:* {MENTION} just checked in",
        f"Need help? Reply in thread or DM {MENTION}",
    ]


def test_linkedin_without_scheme_is_fixed_and_junk_dropped():
    assert "<https://linkedin.com/in/x|LinkedIn>" in build({**FULL, "linkedinProfile": "linkedin.com/in/x"})
    assert "LinkedIn" not in build({**FULL, "linkedinProfile": "n/a"})


def test_send_disables_unfurls():
    with patch.object(vs, "_notifications_disabled", return_value=False), \
         patch.object(vs, "get_user_by_user_id", return_value=None), \
         patch.object(vs, "get_user_by_email", return_value={"id": "abc"}), \
         patch.object(vs, "send_slack") as send:
        assert vs.send_mentor_checkin_notification({**FULL, "slack_user_id": "U123"}) is True
    kwargs = send.call_args.kwargs
    assert kwargs["channel"] == "ask-a-mentor"
    assert kwargs["unfurl_links"] is False and kwargs["unfurl_media"] is False
    assert "\n\n" not in kwargs["message"]
