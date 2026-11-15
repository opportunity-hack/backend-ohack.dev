from datetime import datetime, timedelta

import pytz

from api.surveys.surveys_service import (
    compute_event_mode,
    resolve_survey_mode,
    _parse_event_date,
    _user_doc_id,
    allowed_roles_for,
    ALLOWED_ROLES,
)

TZ = "America/Phoenix"


def _date(offset_days):
    """A YYYY-MM-DD string offset from today in the event timezone."""
    now = datetime.now(pytz.timezone(TZ))
    return (now + timedelta(days=offset_days)).strftime("%Y-%m-%d")


class TestComputeEventMode:
    def test_past_event_is_post(self):
        event = {"start_date": _date(-10), "end_date": _date(-5), "timezone": TZ}
        assert compute_event_mode(event) == "post"

    def test_current_event_is_live(self):
        event = {"start_date": _date(-1), "end_date": _date(1), "timezone": TZ}
        assert compute_event_mode(event) == "live"

    def test_future_event_is_upcoming(self):
        event = {"start_date": _date(5), "end_date": _date(7), "timezone": TZ}
        assert compute_event_mode(event) == "upcoming"

    def test_single_day_event_today_is_live(self):
        today = _date(0)
        assert compute_event_mode({"start_date": today, "end_date": today, "timezone": TZ}) == "live"

    def test_missing_dates_defaults_to_live(self):
        assert compute_event_mode({}) == "live"

    def test_bad_timezone_falls_back(self):
        event = {"start_date": _date(-10), "end_date": _date(-5), "timezone": "Not/AZone"}
        assert compute_event_mode(event) == "post"


class TestHelpers:
    def test_parse_event_date_end_of_day(self):
        tz = pytz.timezone(TZ)
        end = _parse_event_date("2026-03-01", tz, end_of_day=True)
        assert end.hour == 23 and end.minute == 59
        start = _parse_event_date("2026-03-01", tz, end_of_day=False)
        assert start.hour == 0 and start.minute == 0

    def test_parse_event_date_invalid(self):
        tz = pytz.timezone(TZ)
        assert _parse_event_date(None, tz) is None
        assert _parse_event_date("garbage", tz) is None

    def test_user_doc_id_is_deterministic_and_slash_safe(self):
        a = _user_doc_id("evt", "post", "oauth2|slack|abc")
        b = _user_doc_id("evt", "post", "oauth2|slack|abc")
        assert a == b
        assert "/" not in a

    def test_allowed_roles_cover_spec(self):
        for role in ("hacker", "mentor", "judge", "nonprofit", "volunteer", "organizer"):
            assert role in ALLOWED_ROLES


class TestAllowedRolesFor:
    def test_trusted_volunteer_limited_to_selected_roles(self):
        assert allowed_roles_for(True, ["mentor"]) == ["mentor"]
        assert allowed_roles_for(True, ["hacker", "mentor"]) == ["hacker", "mentor"]

    def test_untrusted_can_only_be_nonprofit(self):
        # anonymous / logged-in-but-not-selected
        assert allowed_roles_for(False, []) == ["nonprofit"]
        assert allowed_roles_for(False, ["hacker"]) == ["nonprofit"]



class TestPostOpensAt:
    def _event(self, opens_at):
        return {
            "start_date": _date(-1),
            "end_date": _date(1),
            "timezone": TZ,
            "constraints": {"survey_post_opens_at": opens_at},
        }

    def test_post_once_opens_at_has_passed(self):
        past = (datetime.now(pytz.timezone(TZ)) - timedelta(hours=1)).isoformat()
        assert compute_event_mode(self._event(past)) == "post"

    def test_live_until_opens_at(self):
        future = (datetime.now(pytz.timezone(TZ)) + timedelta(hours=1)).isoformat()
        assert compute_event_mode(self._event(future)) == "live"

    def test_offsetless_value_is_event_local(self):
        past = (datetime.now(pytz.timezone(TZ)) - timedelta(hours=1)).replace(tzinfo=None)
        assert compute_event_mode(self._event(past.isoformat())) == "post"

    def test_bad_value_is_ignored(self):
        assert compute_event_mode(self._event("soon")) == "live"
        assert compute_event_mode(self._event(None)) == "live"


class TestResolveSurveyMode:
    LIVE = {"start_date": _date(-1), "end_date": _date(1), "timezone": TZ}
    ENDED = {"start_date": _date(-5), "end_date": _date(-3), "timezone": TZ}
    UPCOMING = {"start_date": _date(3), "end_date": _date(5), "timezone": TZ}

    def test_no_request_is_natural(self):
        assert resolve_survey_mode(self.LIVE) == "live"
        assert resolve_survey_mode(self.ENDED, "") == "post"

    def test_request_wins_once_started(self):
        assert resolve_survey_mode(self.LIVE, "post") == "post"
        assert resolve_survey_mode(self.ENDED, "LIVE") == "live"

    def test_upcoming_cannot_be_forced(self):
        assert resolve_survey_mode(self.UPCOMING, "post") == "upcoming"

    def test_unknown_request_is_ignored(self):
        assert resolve_survey_mode(self.LIVE, "complete") == "live"
