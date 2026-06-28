from datetime import datetime, timedelta

import pytz

from api.surveys.surveys_service import (
    compute_event_mode,
    _parse_event_date,
    _user_doc_id,
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
