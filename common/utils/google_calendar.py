"""Google Calendar client for scheduling a meeting with a Google Meet link.

Used by ``api/meet`` when an admin finalizes a scheduling poll. The event is
created on an @ohack.org calendar, so Google sends the invitations (and the
Meet link) itself — nothing here goes through Resend.

Setup (one time, Google Workspace admin):
1. Create a service account in Google Cloud, enable the Google Calendar API.
2. In the Workspace Admin console grant that service account's client id
   domain-wide delegation for the scope in ``SCOPES``.
3. Set ``GOOGLE_CALENDAR_SA_CONFIG`` (the service account JSON, as a string,
   same style as ``FIREBASE_CERT_CONFIG``) and ``GOOGLE_CALENDAR_ORGANIZER``
   (the @ohack.org user the event is created as). ``GOOGLE_CALENDAR_ID`` is
   optional and defaults to that user's primary calendar.

A plain service account (no delegation) cannot invite attendees or create a
Meet link, which is why the organizer is impersonated.

google-auth is imported lazily: it ships with firebase-admin, and a lazy
import keeps this module importable in tests without credentials.
"""
import json
import os
from typing import Any, Dict, List, Optional

from common.log import get_logger

logger = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API_BASE = "https://www.googleapis.com/calendar/v3"
HTTP_TIMEOUT_SECONDS = 20


class CalendarError(Exception):
    """Google Calendar refused or failed the request."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def organizer() -> str:
    return (os.environ.get("GOOGLE_CALENDAR_ORGANIZER") or "").strip()


def is_configured() -> bool:
    return bool(organizer() and (os.environ.get("GOOGLE_CALENDAR_SA_CONFIG") or "").strip())


def _calendar_id() -> str:
    return (os.environ.get("GOOGLE_CALENDAR_ID") or "").strip() or "primary"


def _session() -> Any:
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    info = json.loads(os.environ["GOOGLE_CALENDAR_SA_CONFIG"])
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    ).with_subject(organizer())
    return AuthorizedSession(credentials)


def _result(event: Dict[str, Any]) -> Dict[str, Any]:
    meet_link = event.get("hangoutLink") or ""
    if not meet_link:
        for entry in (event.get("conferenceData") or {}).get("entryPoints") or []:
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri") or ""
                break
    return {
        "event_id": event.get("id") or "",
        "meet_link": meet_link,
        "html_link": event.get("htmlLink") or "",
    }


def create_meet_event(
    event_id: str,
    summary: str,
    description: str,
    start_utc: str,
    end_utc: str,
    attendees: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Create a calendar event with a Google Meet link and email every
    attendee. ``start_utc`` / ``end_utc`` are RFC 3339 UTC strings.

    ``event_id`` is caller-chosen (base32hex characters) so a retry or a
    double click cannot create a second event: Google answers 409 for an id
    it already has, and the existing event is returned instead.

    Returns ``{event_id, meet_link, html_link}``. Raises ``CalendarError``.
    """
    if not is_configured():
        raise CalendarError("Google Calendar is not configured")
    body = {
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_utc, "timeZone": "UTC"},
        "end": {"dateTime": end_utc, "timeZone": "UTC"},
        "attendees": [
            {"email": a["email"], **({"displayName": a["name"]} if a.get("name") else {})}
            for a in attendees
        ],
        # Respondents gave their email to the organizer, not to each other.
        "guestsCanSeeOtherGuests": False,
        "conferenceData": {
            "createRequest": {
                "requestId": event_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    url = "%s/calendars/%s/events" % (API_BASE, _calendar_id())
    try:
        session = _session()
        response = session.post(
            url,
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json=body,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if response.status_code == 409:
            logger.info("Calendar event %s already exists; reusing it", event_id)
            response = session.get(
                "%s/%s" % (url, event_id), timeout=HTTP_TIMEOUT_SECONDS
            )
    except CalendarError:
        raise
    except Exception as e:  # auth, network, malformed credentials
        logger.exception("Google Calendar request failed: %s", type(e).__name__)
        raise CalendarError("Could not reach Google Calendar (%s)" % type(e).__name__)
    if response.status_code >= 300:
        detail = ""
        try:
            detail = (response.json().get("error") or {}).get("message") or ""
        except Exception:
            detail = ""
        logger.error(
            "Google Calendar returned %s: %s", response.status_code, detail
        )
        raise CalendarError(
            detail or "Google Calendar returned HTTP %s" % response.status_code,
            status=response.status_code,
        )
    return _result(response.json())
