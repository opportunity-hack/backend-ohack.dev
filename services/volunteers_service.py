from typing import Dict, List, Optional, Union, Any, Tuple
import uuid
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pytz
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import lru_cache
from ratelimiter import RateLimiter
from db.db import get_db
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from common.utils.slack import get_slack_user_by_email, send_slack
from common.utils.firebase import get_user_by_user_id, get_user_by_email
from common.log import get_logger, info, debug, warning, error, exception
from services.resend_utils import send_with_retry, MAX_ATTEMPTS as RESEND_MAX_ATTEMPTS
from common.utils.redis_cache import redis_cached, delete_cached, clear_pattern, get_cached, set_cached
from common.utils.oauth_providers import SLACK_PREFIX, normalize_slack_user_id, is_oauth_user_id, is_slack_user_id, extract_slack_user_id
import os
import requests
import html
import resend
import stripe
import markdown
import re
import base64
import qrcode
import io

logger = get_logger("services.volunteers_service")

# Volunteer-doc fields that only staff / system flows may write. A self-service
# application submit or update must never change these — the applicant's own
# payload is not authoritative. Owners: approval -> update_volunteer_selection();
# check-in/out -> mentor_checkin()/mentor_checkout(); refund bookkeeping ->
# refund_hacker_deposit() and the Stripe webhook handlers.
#
# Deliberately NOT listed: deposit_amount_cents, deposit_disposition and
# stripe_payment_intent_id — the hacker form legitimately sets those when it
# returns from Stripe Checkout, which happens on the /update path.
STAFF_OWNED_VOLUNTEER_FIELDS = frozenset({
    'isSelected',
    'isCheckedIn', 'checkedIn', 'checkInTime', 'checkInTimeList',
    'checkOutTime', 'checkoutTimeList',
    'checkedInAt', 'checkedInBy',
    # Audit trail written by api/checkin reset_checkins().
    'checkin_reset_history',
    'deposit_status', 'deposit_refund_id', 'deposit_refund_amount_cents',
    'deposit_refunded_at', 'deposit_refunded_by', 'deposit_refund_status_msg',
    'certificates', 'sent_emails',
    # Written only by the hacker auto-approve hook (_apply_roster_decision).
    'auto_approved', 'auto_approved_at',
})

def _notifications_disabled() -> bool:
    """
    True when outbound notifications (Slack, Resend email) must be suppressed.
    Mirrors the ENVIRONMENT=test gate get_db() uses for MockFirestore — without
    this, unit tests exercising create_or_update_volunteer posted REAL Slack
    messages and attempted REAL Resend sends.
    """
    return os.environ.get("ENVIRONMENT") == "test"


def _generate_volunteer_id() -> str:
    """Generate a unique ID for a volunteer."""
    return str(uuid.uuid4())

def _get_current_timestamp() -> str:
    """Get current ISO timestamp in Arizona timezone."""
    az_timezone = pytz.timezone('US/Arizona')
    return datetime.now(az_timezone).isoformat()

@redis_cached(prefix="volunteer:by_user_id", ttl=2)
def get_volunteer_by_user_id(user_id: str, event_id: str, volunteer_type: str) -> Optional[Dict[str, Any]]:
    """Get volunteer by user ID, event ID, and volunteer type."""
    db = get_db()
    volunteers = db.collection('volunteers').where('user_id', '==', user_id) \
                                          .where('event_id', '==', event_id) \
                                          .where('volunteer_type', '==', volunteer_type) \
                                          .limit(1).stream()
    
    for volunteer in volunteers:
        return volunteer.to_dict()
    return None

def get_volunteer_application_count_by_availability_timeslot(event_id: str) -> Dict[str, int]:
    """
    Get the count of volunteer applications grouped by availability timeslot.
    
    Args:
        event_id: The event ID
        
    Returns:
        Dictionary with timeslot as key and count as value
    """
    db = get_db()
    query = db.collection('volunteers').where('event_id', '==', event_id).where('volunteer_type', '==', 'volunteer')
    
    logger.info(f"Querying volunteer applications by availability timeslot {event_id}")
    # Aggregate by availability timeslot
    results = query.stream()
    timeslot_counts = {}
    for doc in results:
        data = doc.to_dict()
        availability = data.get('availableDays', '')
        if availability:            
            for slot in availability:
                if slot not in timeslot_counts:
                    timeslot_counts[slot] = 0
                timeslot_counts[slot] += 1
    logger.info(f"Counted {len(timeslot_counts)} unique availability timeslots for event {event_id}")
    return timeslot_counts

@redis_cached(prefix="volunteer:by_email", ttl=2)
def get_volunteer_by_email(email: str, event_id: str, volunteer_type: str) -> Optional[Dict[str, Any]]:
    """Get volunteer by email, event ID, and volunteer type."""
    db = get_db()
    volunteers = db.collection('volunteers').where('email', '==', email) \
                                          .where('event_id', '==', event_id) \
                                          .where('volunteer_type', '==', volunteer_type) \
                                          .limit(1).stream()

    for volunteer in volunteers:
        return volunteer.to_dict()
    return None


def find_volunteer_by_caller_identity(propel_user_id: str, event_id: str, volunteer_type: str) -> Optional[Dict[str, Any]]:
    """
    Resolve the calling user's volunteer doc for an event, trying every
    identity shape a doc may have been stored under:

      1. raw PropelAuth UUID  — self-submitted apps store auth_user.user_id
         (the propel UUID) in the doc's `user_id` field. The common case.
      2. PropelAuth email     — docs created by admin/import flows, or legacy
         self-submits, where `user_id` holds something else. The email comes
         from the VERIFIED token identity (never the form payload, which would
         let a caller hijack someone else's application by typing their email).
      3. OAuth user_id        — legacy docs that stored oauth2|slack|... .

    This is the single resolver shared by the GET application route
    (handle_get), the submit/update path (create_or_update_volunteer), and the
    mentor gate (_find_mentor_volunteer). Keeping read and write on the SAME
    resolver matters: when the read path found a doc the write path couldn't,
    an application edit fell into the create branch and spawned a duplicate
    isSelected=False doc, orphaning the approved one.
    """
    if not propel_user_id:
        return None

    volunteer = get_volunteer_by_user_id(propel_user_id, event_id, volunteer_type)
    if volunteer:
        return volunteer

    email = oauth_user_id = None
    try:
        # Lazy import: users_service lazily imports this module, so a
        # top-level import here would risk a cycle.
        from services.users_service import get_propel_user_details_by_id
        details = get_propel_user_details_by_id(propel_user_id) or ()
        email = details[0] if len(details) > 0 else None
        oauth_user_id = details[1] if len(details) > 1 else None
    except Exception as e:
        warning(logger, "Could not resolve caller identity via PropelAuth",
                propel_user_id=propel_user_id, exc_info=e)

    if email:
        volunteer = get_volunteer_by_email(email, event_id, volunteer_type)
    if volunteer is None and oauth_user_id and oauth_user_id != propel_user_id:
        volunteer = get_volunteer_by_user_id(oauth_user_id, event_id, volunteer_type)
    return volunteer


def get_volunteer_self_status(propel_user_id: str, event_id: str, volunteer_type: str) -> Dict[str, Any]:
    """
    Generic lightweight self-check, keyed by volunteer_type, for
    GET /api/volunteer/<event_id>/me. Mirrors api.mentors.mentors_service.
    get_mentor_self_status's shape/leanness but works for any volunteer_type
    (introduced for type=hacker, used by the team dashboard + Hackers' Choice
    eligibility gate). The volunteer subset returned is intentionally lean —
    only what the caller already knows about themselves.
    """
    doc = find_volunteer_by_caller_identity(propel_user_id, event_id, volunteer_type)
    is_selected = bool(doc and doc.get("isSelected"))
    return {
        f"is_{volunteer_type}": is_selected,
        "volunteer": {"name": doc.get("name"), "isSelected": True} if is_selected else None,
    }

# Function to clear all caches related to a volunteer
def _clear_volunteer_caches(user_id: str, email: str, event_id: str, volunteer_type: str):
    """Clear all caches related to a specific volunteer."""
    try:
        # Clear cache for specific user lookup
        user_key = f"volunteer:by_user_id:{user_id}:{event_id}:{volunteer_type}"
        delete_cached(user_key)
    except Exception as e:
        warning(logger, "Failed to clear user cache", user_key=user_key, exc_info=e)
    
    try:
        # Clear cache for specific email lookup
        email_key = f"volunteer:by_email:{email}:{event_id}:{volunteer_type}"
        delete_cached(email_key)
    except Exception as e:
        warning(logger, "Failed to clear email cache", email_key=email_key, exc_info=e)
    
    try:
        # Clear event-based volunteer caches
        event_key = f"volunteer:by_event:{event_id}:{volunteer_type}"
        clear_pattern(f"{event_key}*")
    except Exception as e:
        warning(logger, "Failed to clear event cache pattern", event_key=event_key, exc_info=e)
    
    debug(logger, f"Attempted to clear volunteer caches for user_id={user_id}, email={email}, event_id={event_id}, volunteer_type={volunteer_type}")

@redis_cached(prefix="volunteer:by_event", ttl=2)
def get_volunteers_by_event(
    event_id: str, 
    volunteer_type: str, 
    page: int = 1, 
    limit: int = 20, 
    selected: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """
    Get volunteers for a specific event with pagination and filtering options.
    
    Args:
        event_id: The event ID
        volunteer_type: The type of volunteer (mentor, sponsor, judge)
        page: Page number (starting from 1)
        limit: Number of records per page
        selected: Filter by selection status (True/False)
        
    Returns:
        List of volunteer records
    """
    db = get_db()
    query = db.collection('volunteers').where('event_id', '==', event_id) \
                                     .where('volunteer_type', '==', volunteer_type)
    
    if selected is not None:
        query = query.where('isSelected', '==', selected)
    
    # Calculate pagination
    offset = (page - 1) * limit
    
    # Get results with pagination
    volunteers = list(query.limit(limit).offset(offset).stream())
    return [vol.to_dict() for vol in volunteers]

# reCAPTCHA verification function
def verify_recaptcha(token: str) -> bool:
    """
    Verify a reCAPTCHA token with Google's API.
    
    Args:
        token: The reCAPTCHA token to verify
        
    Returns:
        True if verification succeeded, False otherwise
    """
    recaptcha_key = os.environ.get('GOOGLE_CAPTCHA_SECRET_KEY')
    if not recaptcha_key:
        warning(logger, "No GOOGLE_CAPTCHA_SECRET_KEY set, skipping verification")
        return True
        
    url = "https://www.google.com/recaptcha/api/siteverify"
    data = {
        "secret": recaptcha_key,
        "response": token
    }
    
    try:
        response = requests.post(url, data=data)
        result = response.json()
        return result.get("success", False)
    except Exception as e:
        exception(logger, "Error verifying recaptcha", exc_info=e)
        return False

def send_volunteer_confirmation_email(first_name: str, last_name: str, email: str, volunteer_type: str,
    calendar_attachments: Optional[List[Dict[str, Any]]] = None, event_id: Optional[str] = None,
    volunteer_id: Optional[str] = None, shifts: Optional[List[str]] = None,
    event: Optional[Dict[str, Any]] = None, roster_outcome: Optional[str] = None) -> Optional[str]:
    """
    Send a confirmation email to the volunteer who submitted the form.
    
    Args:
        first_name: First name of the volunteer
        last_name: Last name of the volunteer
        email: Email address of the volunteer
        volunteer_type: Type of volunteer (mentor, sponsor, judge)
        calendar_attachments: Optional list of calendar attachments
        event_id: Event ID for generating links
        shifts: Optional human-readable shift/time-slot lines (see
            split_availability_slots) rendered as a "Your shifts" list
        event: Optional hackathon summary (see _hackathon_for_confirmation)
            rendered as a "Your event" block — title, dates, venue, event link
        roster_outcome: "approved" | "waitlisted" | None — set when hacker
            auto-approve decided this application (see _hacker_roster_decision)
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    if _notifications_disabled():
        info(logger, "ENVIRONMENT=test — skipping volunteer confirmation email", email=email)
        return None

    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        error(logger, "Missing required environment variable", var_name="RESEND_WELCOME_EMAIL_KEY")
        return False

    resend.api_key = resend_api_key

    try:
        volunteer_type_readable = volunteer_type.capitalize()
        is_reviewed_role = volunteer_type.lower() in ("mentor", "judge")
        is_hacker = volunteer_type.lower() == "hacker"
        confirmed = is_hacker and roster_outcome == "approved"
        waitlisted = is_hacker and roster_outcome == "waitlisted"
        event_slug_raw = str((event or {}).get("event_id") or event_id or "")
        # Format the name properly, handling empty strings
        full_name = first_name + " " + last_name if first_name and last_name else first_name or last_name or "Volunteer"

        # Check if we have calendar attachments
        calendar_note = ""
        if calendar_attachments and len(calendar_attachments) > 0:
            calendar_note = """
            <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #3498db; margin: 15px 0;">
                <p style="margin: 0; font-weight: bold; color: #2c3e50;">📅 Calendar Invites Attached</p>
                <p style="margin: 5px 0 0 0; color: #34495e;">Your volunteering time slots have been attached as calendar files (.ics). Click on the attachments to add them to your Google Calendar, Outlook, or Apple Calendar.</p>
            </div>"""
            info(logger, "Including calendar attachments in email", email=email, attachment_count=len(calendar_attachments))

        # Which event, when and where — from the hackathon record, so an
        # in-person event never reads as "Virtual" (the old .ics default).
        event_block = ""
        if event and (event.get("title") or event.get("location")):
            event_title = html.escape(_calendar_event_name(event))
            event_dates = html.escape(_format_event_date_range(event.get("start_date"), event.get("end_date")))
            event_location = html.escape(str(event.get("location") or "").strip())
            event_slug = html.escape(str(event.get("event_id") or event_id or ""))
            dates_line = (
                f'<p style="margin: 6px 0 0 0; font-size: 15px; color: #2c3e50;">📅 {event_dates}</p>'
                if event_dates else ""
            )
            location_line = (
                f'<p style="margin: 4px 0 0 0; font-size: 15px; color: #2c3e50;">📍 {event_location}</p>'
                if event_location else ""
            )
            event_link = (
                f'<p style="margin: 10px 0 0 0; font-size: 14px;"><a href="https://www.ohack.dev/hack/{event_slug}" style="color: #1B3A6B; font-weight: bold; text-decoration: none;">Event page: schedule, venue &amp; parking →</a></p>'
                if event_slug else ""
            )
            event_block = f"""
            <div style="background-color: #f8f9fa; padding: 16px 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #E2552E;">
                <p style="margin: 0 0 6px 0; font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; color: #777;">Your event</p>
                <p style="margin: 0; font-size: 17px; font-weight: bold; color: #1B3A6B;">{event_title}</p>
                {dates_line}
                {location_line}
                {event_link}
            </div>"""

        # The shifts/time slots they picked, as text. Mentors and volunteers both
        # choose structured slots; judges send free text (no block then).
        shifts_block = ""
        if shifts:
            shift_items = "".join(
                f'<li style="margin: 6px 0; color: #2c3e50;">{html.escape(shift)}</li>' for shift in shifts
            )
            shifts_block = f"""
            <div style="background-color: #f8f9fa; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #1B3A6B;">
                <h3 style="color: #1B3A6B; margin: 0 0 10px 0; font-size: 18px;">🗓️ Your shifts</h3>
                <ul style="margin: 0; padding-left: 20px; font-size: 15px;">{shift_items}</ul>
                <p style="margin: 12px 0 0 0; font-size: 14px; color: #555;">Need to change a shift? Reopen your application on the event page and update your availability.</p>
            </div>"""

        # Pending-review banner (mentor + judge only — these roles are reviewed by staff)
        pending_review_banner = ""
        if is_reviewed_role:
            pending_review_banner = f"""
            <div style="background-color: #fff8e1; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
                <h3 style="color: #92400e; margin: 0 0 8px 0; font-size: 18px;">⏳ Your application is pending review</h3>
                <p style="margin: 0 0 8px 0; font-size: 15px; color: #78350f;">
                    Our staff reviews every {volunteer_type_readable.lower()} application by hand. <strong>Reviews typically take up to a week.</strong> You don't need to do anything else right now — we'll email you when your application is approved or if we have follow-up questions.
                </p>
            </div>"""

        # Hacker auto-approve outcome (Nov 2026). Confirmed hackers get their
        # check-in QR + team links up front; waitlisted ones a calm "we'll email
        # you" — never a rejection.
        roster_banner = ""
        if confirmed:
            slug = html.escape(event_slug_raw)
            roster_banner = f"""
            <div style="background-color: #e8f5e9; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #27ae60;">
                <h3 style="color: #1e7e34; margin: 0 0 8px 0; font-size: 18px;">✅ You're confirmed — you're on the roster</h3>
                <p style="margin: 0 0 12px 0; font-size: 15px; color: #2c3e50;">
                    There's nothing else to wait for. Your application page now shows your personal <strong>check-in QR code</strong> — have it ready on your phone when you arrive. Next, find or set up your team.
                </p>
                <p style="margin: 0; font-size: 15px;">
                    <a href="https://www.ohack.dev/hack/{slug}/hacker-application" style="color: #1B3A6B; font-weight: bold; text-decoration: none;">Your check-in QR code →</a>
                    &nbsp;&nbsp;
                    <a href="https://www.ohack.dev/hack/{slug}/manageteam" style="color: #1B3A6B; font-weight: bold; text-decoration: none;">Set up your team →</a>
                </p>
            </div>"""
        elif waitlisted:
            roster_banner = """
            <div style="background-color: #fff8e1; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
                <h3 style="color: #92400e; margin: 0 0 8px 0; font-size: 18px;">⏳ You're on the waitlist</h3>
                <p style="margin: 0; font-size: 15px; color: #78350f;">
                    The event has reached capacity, so we've saved your application on the waitlist. If a spot opens up we'll email you right away — thank you for wanting to build with us.
                </p>
            </div>"""

        # Generate volunteer type specific content
        volunteer_specific_content = ""
        if volunteer_type.lower() == "mentor":
            volunteer_specific_content = f"""
            <div style="background-color: #e8f5e8; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #27ae60;">
                <h3 style="color: #27ae60; margin-top: 0;">🚀 Once approved — here's what to expect as a mentor</h3>
                <p style="margin-bottom: 12px;">Mentor certificates at Opportunity Hack are awarded for <strong>proactive help</strong>, not attendance. That means: reaching out to teams in Slack, reviewing code, giving feedback on presentations, and sitting with teams to unblock them — not just being available.</p>
                <p style="margin-bottom: 15px;">When you're ready to help teams during the event, please check in using the button below:</p>
                <div style="text-align: center; margin: 20px 0;">
                    <a href="https://www.ohack.dev/hack/{event_id}/mentor-checkin"
                       style="background-color: #27ae60; color: white; padding: 12px 25px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
                        ✅ Mentor Check-In
                    </a>
                </div>
                <p style="margin-bottom: 10px;">Learn more about the mentor role and what to expect:</p>
                <div style="text-align: center;">
                    <a href="https://www.ohack.dev/about/mentors"
                       style="color: #27ae60; text-decoration: none; font-weight: bold;">
                        📖 Mentor Guidelines & Information
                    </a>
                </div>
            </div>"""
        elif volunteer_type.lower() == "volunteer":
            # Event volunteers (check-in, food, cleanup, photo, judging support).
            # Not staff-reviewed like mentors/judges, but organizers still put
            # them on the roster before the event — the guide explains what
            # "approved" unlocks (QR code, roster, share card) and how check-in
            # works for anyone on the registration shift.
            volunteer_specific_content = """
            <div style="background-color: #eef2f8; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #1B3A6B;">
                <h3 style="color: #1B3A6B; margin-top: 0;">🙌 What happens next as an event volunteer</h3>
                <p style="margin-bottom: 12px;">Organizers confirm volunteers before the event. Once you're on the roster, your application page shows your personal <strong>check-in QR code</strong> — have it ready on your phone when you arrive.</p>
                <p style="margin-bottom: 15px;">Signed up for the registration shift? The volunteer guide walks through exactly how check-in works, what to do when someone isn't on the list, and a before/during/after checklist for every role.</p>
                <div style="text-align: center;">
                    <a href="https://www.ohack.dev/about/volunteers"
                       style="background-color: #1B3A6B; color: white; padding: 12px 25px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
                        📖 Read the Volunteer Guide
                    </a>
                </div>
            </div>"""
        elif volunteer_type.lower() == "judge":
            volunteer_specific_content = """
            <div style="background-color: #fff3cd; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #ffc107;">
                <h3 style="color: #856404; margin-top: 0;">⚖️ Once approved — here's what to expect as a judge</h3>
                <p style="margin-bottom: 12px;">Judges are expected to review each assigned project in depth and ask questions that probe gaps in the judging criteria (Scope, Documentation, Polish, Security). Strong judging makes the event meaningful for the teams and the nonprofits they're building for.</p>
                <p style="margin-bottom: 15px;">Learn about the judging process, evaluation criteria, and what to expect:</p>
                <div style="text-align: center;">
                    <a href="https://www.ohack.dev/hackathon-judging-criteria"
                       style="background-color: #ffc107; color: #212529; padding: 12px 25px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
                        📋 Judging Criteria
                    </a>
                </div>
            </div>"""

        subject_prefix = "[Pending Review] " if is_reviewed_role else ""
        subject = f"{subject_prefix}Thanks for your Opportunity Hack {volunteer_type_readable} application"
        if is_reviewed_role:
            intro_line = f"Thanks for applying as a <strong>{volunteer_type_readable}</strong> for Opportunity Hack — your application has been received."
        elif confirmed:
            subject = "[Confirmed] You're in — Opportunity Hack Hacker application"
            intro_line = "Thanks for applying as a <strong>Hacker</strong> for Opportunity Hack — you're in, and we can't wait to build with you."
        elif waitlisted:
            intro_line = "Thanks for applying as a <strong>Hacker</strong> for Opportunity Hack — we've received your application."
        else:
            intro_line = f"Thank you for applying as a <strong>{volunteer_type_readable}</strong> for Opportunity Hack. We've received your information and our team will review it shortly."

        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "subject": subject,
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border-radius: 5px;">
                <div style="text-align: center; margin-bottom: 20px;">
                    <img src="https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Dark_Blue.png" alt="Opportunity Hack Logo" style="max-width: 150px;">
                </div>
                <h2 style="color: #3498db; text-align: center;">Thanks for your Opportunity Hack {volunteer_type_readable} application!</h2>
                <p style="font-size: 16px;">Hello {full_name},</p>
                <p style="font-size: 16px;">{intro_line}</p>
                {event_block}
                {roster_banner}
                {pending_review_banner}
                {shifts_block}
                {calendar_note}
                {volunteer_specific_content}
                <p style="font-size: 16px;">In the meantime, feel free to explore our website and learn more about our mission and past events.</p>
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                    <p style="font-size: 14px; color: #777;">The Opportunity Hack Team</p>
                    <p style="font-size: 14px; color: #777;">Website: <a href="https://ohack.dev" style="color: #3498db;">ohack.dev</a></p>
                </div>
            </div>
            """
        }
        
        # Only add attachments if they exist
        if calendar_attachments and len(calendar_attachments) > 0:
            params["attachments"] = calendar_attachments
        
        email_result = resend.Emails.send(params)
        resend_id = email_result.get('id') if isinstance(email_result, dict) else getattr(email_result, 'id', None)
        info(logger, "Sent confirmation email to volunteer", email=email,
             attachment_count=len(calendar_attachments) if calendar_attachments else 0,
             resend_id=resend_id)
        if resend_id and volunteer_id:
            try:
                subject = params.get('subject', '')
                sent_record = {
                    'resend_id': resend_id,
                    'subject': subject,
                    'timestamp': _get_current_timestamp(),
                    'sent_by': 'system',
                    'recipient_type': 'application_confirmation',
                }
                get_db().collection('volunteers').document(volunteer_id).update(
                    {'sent_emails': firestore.ArrayUnion([sent_record])}
                )
                info(logger, "Tracked confirmation email in sent_emails",
                     volunteer_id=volunteer_id, resend_id=resend_id)
            except Exception as track_err:
                warning(logger, "Failed to track confirmation email in sent_emails",
                        exc_info=track_err, volunteer_id=volunteer_id)
        return resend_id
    except Exception as e:
        exception(logger, "Error sending email via Resend", exc_info=e, email=email)
        return None

def send_admin_notification_email(volunteer_data: Dict[str, Any], is_update: bool = False) -> bool:
    """
    Send a notification email to the admin when a volunteer form is submitted or updated.
    
    Args:
        volunteer_data: The volunteer data
        is_update: Whether this is an update to an existing volunteer
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    if _notifications_disabled():
        info(logger, "ENVIRONMENT=test — skipping admin notification email")
        return False

    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        error(logger, "Missing required environment variable", var_name="RESEND_WELCOME_EMAIL_KEY")
        return False

    resend.api_key = resend_api_key
    
    try:
        action_type = "updated" if is_update else "submitted"
        first_name = volunteer_data.get('firstName', '')
        last_name = volunteer_data.get('lastName', '')
        email = volunteer_data.get('email', '')
        volunteer_type = volunteer_data.get('volunteer_type', '')
        
        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": ["greg@ohack.org"],
            "subject": f"Volunteer form {action_type}: {first_name} {last_name}",
            "html": f"""
            <div>
                <h2>Volunteer Form {action_type.capitalize()}</h2>
                <p>A volunteer form has been {action_type}:</p>
                <p>Name: {first_name} {last_name}<br>
                   Email: {email}<br>
                   Type: {volunteer_type}</p>
            </div>
            """
        }
        
        resend.Emails.send(params)
        info(logger, "Sent admin notification email", email=email, is_update=is_update)
        return True
    except Exception as e:
        exception(logger, "Error sending admin email via Resend", exc_info=e, email=email)
        error(logger, "Failed to send admin notification email", exc_info=e)

        return False

def send_slack_volunteer_notification(volunteer_data: Dict[str, Any], is_update: bool = False) -> bool:
    """
    Send a rich Block Kit notification to Slack when a volunteer form is submitted or updated.

    Args:
        volunteer_data: The volunteer data
        is_update: Whether this is an update to an existing volunteer

    Returns:
        True if notification was sent successfully, False otherwise
    """
    if _notifications_disabled():
        info(logger, "ENVIRONMENT=test — skipping Slack volunteer notification")
        return False

    import datetime as _dt

    # --- Derive display values ---
    first_name = volunteer_data.get('firstName', '')
    last_name = volunteer_data.get('lastName', '')
    name = volunteer_data.get('name', '')
    display_name = name or f"{first_name} {last_name}".strip() or "Unknown"

    email = volunteer_data.get('email', '')
    volunteer_type = volunteer_data.get('volunteer_type', 'volunteer')
    event_id = volunteer_data.get('event_id', '')

    type_emoji = {
        'mentor': ':brain:',
        'judge': ':scales:',
        'volunteer': ':raised_hands:',
        'sponsor': ':star:',
        'hacker': ':computer:',
    }
    emoji = type_emoji.get(volunteer_type, ':raised_hands:')
    action_label = "Updated" if is_update else "New"
    role_label = volunteer_type.capitalize() if volunteer_type else "Volunteer"

    # --- Fallback plain-text (required by Slack for notifications/accessibility) ---
    fallback_text = f"{action_label} {role_label} Application — {display_name} ({email})"

    # --- Build Block Kit blocks ---
    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"{emoji}  {action_label} {role_label} Application", "emoji": True}
    })

    blocks.append({"type": "divider"})

    # Core fields (two-column layout)
    fields = [
        {"type": "mrkdwn", "text": f"*Name:*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Email:*\n{email}"},
        {"type": "mrkdwn", "text": f"*Role:*\n{role_label}"},
        {"type": "mrkdwn", "text": f"*Event:*\n{event_id or '—'}"},
    ]
    blocks.append({"type": "section", "fields": fields})

    # Additional details (only if present)
    detail_lines = []
    company = volunteer_data.get('company', '')
    if company:
        detail_lines.append(f"*Company:* {company}")

    linkedin = volunteer_data.get('linkedinProfile', '')
    if linkedin:
        detail_lines.append(f"*LinkedIn:* <{linkedin}|Profile>")

    expertise = volunteer_data.get('expertise', '')
    if expertise:
        detail_lines.append(f"*Expertise:* {expertise}")

    sw_specifics = volunteer_data.get('softwareEngineeringSpecifics', '')
    if sw_specifics:
        detail_lines.append(f"*SW Engineering:* {sw_specifics}")

    in_person = volunteer_data.get('inPerson')
    if in_person is not None:
        in_person_label = "Yes :office:" if in_person else "No (remote) :globe_with_meridians:"
        detail_lines.append(f"*In-Person:* {in_person_label}")

    availability = volunteer_data.get('availability', '')
    if availability:
        # Truncate long availability strings for readability
        avail_display = availability if len(availability) <= 200 else availability[:200] + "…"
        detail_lines.append(f"*Availability:* {avail_display}")

    available_days = volunteer_data.get('availableDays', '')
    if available_days:
        days_str = str(available_days)
        if len(days_str) > 500:
            days_str = days_str[:497] + "…"
        detail_lines.append(f"*Available Days:* {days_str}")

    skills = volunteer_data.get('skills', '')
    if skills:
        skills_str = str(skills)[:500] if len(str(skills)) > 500 else str(skills)
        detail_lines.append(f"*Skills:* {skills_str}")

    experience = volunteer_data.get('experience', '')
    if experience:
        exp_str = str(experience)[:500] if len(str(experience)) > 500 else str(experience)
        detail_lines.append(f"*Experience:* {exp_str}")

    if detail_lines:
        # Slack section text cap is 3000 chars; split if needed (max 50 blocks total).
        section_text = "\n".join(detail_lines)
        if len(section_text) <= 2900:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section_text}})
        else:
            # Split into ≤2900-char chunks
            chunk, chunks = [], []
            for line in detail_lines:
                candidate = "\n".join(chunk + [line])
                if len(candidate) > 2900 and chunk:
                    chunks.append("\n".join(chunk))
                    chunk = [line]
                else:
                    chunk.append(line)
            if chunk:
                chunks.append("\n".join(chunk))
            for c in chunks[:48]:  # leave room for header + footer blocks
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": c}})

    # Context footer — timestamp + Slack workspace lookup result
    context_elements = [
        {"type": "mrkdwn", "text": f"Submitted {_dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}
    ]
    slack_user_id = volunteer_data.get('slack_user_id', '')
    if slack_user_id:
        context_elements.append({"type": "mrkdwn", "text": f":white_check_mark: Found in Slack workspace (<@{slack_user_id}>)"})
    else:
        context_elements.append({"type": "mrkdwn", "text": ":x: Not yet found in Slack workspace"})

    blocks.append({"type": "context", "elements": context_elements})

    # --- Send ---
    try:
        send_slack(
            message=fallback_text,
            channel="volunteer-applications",
            icon_emoji=emoji,
            username="Volunteer Bot",
            blocks=blocks
        )
        info(logger, "Sent Slack notification about volunteer", email=email, is_update=is_update)
        return True
    except Exception as e:
        exception(logger, "Error sending Slack notification", exc_info=e, email=email, volunteer_type=volunteer_type)
        return False

_STRUCTURED_SLOT_RE = re.compile(r'[A-Za-z]+day,\s*[A-Za-z]{3,}\s+\d{1,2}')
_EXPLICIT_RANGE_RE = re.compile(
    r'(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)',
    re.IGNORECASE,
)


def split_availability_slots(availability: Optional[str]) -> List[str]:
    """Split a structured availability string into its slot strings.

    Returns [] for empty or free-text availability (judges). The slot strings
    are the exact human-readable text the forms produce, e.g.
    "Saturday, Nov 14: Doors Open & Registration - 📋 Check-in Registration (8:00am - 11:00am MST)"
    or "Saturday, Nov 14: 🌅 Early Morning (7am - 9am MST)".
    """
    if not availability or not _STRUCTURED_SLOT_RE.search(availability):
        return []
    # Split on commas that are followed by a weekday — the comma inside
    # "Saturday, Nov 14" must not split.
    parts = re.split(r',\s*(?=[A-Za-z]+day,)', availability)
    return [part.strip() for part in parts if part.strip()]


def _parse_explicit_time_range(text: str) -> Optional[Dict[str, str]]:
    """"(8:00am - 11:00am MST)" → {"start": "08:00", "end": "11:00"}; None if absent."""
    match = _EXPLICIT_RANGE_RE.search(text or "")
    if not match:
        return None

    def to_24h(hour: str, minute: Optional[str], meridiem: str) -> str:
        h = int(hour) % 12
        if meridiem.lower() == "pm":
            h += 12
        return f"{h:02d}:{int(minute or 0):02d}"

    sh, sm, sap, eh, em, eap = match.groups()
    return {"start": to_24h(sh, sm, sap), "end": to_24h(eh, em, eap)}


# Event timezone for the .ics invites. The hackathon's own `timezone` (admin
# Overview section) is the source of truth: the application forms label every
# slot with that zone's abbreviation ("(8:00am - 11:00am MST)"). The
# abbreviation is only a hint for when the hackathon record can't be read, and
# DEFAULT_EVENT_TIMEZONE mirrors the frontend's DEFAULT_EVENT_TIMEZONE
# (src/lib/timezoneUtils.js) — the zone the forms label slots with when the
# admin never set one.
DEFAULT_EVENT_TIMEZONE = "America/Phoenix"
_TZ_ABBREVIATION_TO_IANA = {
    "MST": "America/Phoenix",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}
# The abbreviation closing a slot's parenthesised time range: "(… 11:00am MST)".
_SLOT_TZ_ABBR_RE = re.compile(r"\s([A-Z]{2,4})\s*\)")


def _resolve_event_tz(tz_name: Optional[str], availability: Optional[str] = None) -> ZoneInfo:
    """The zone a slot's wall-clock times are in: the hackathon's `timezone`
    first, then the abbreviation in the slot text, then the default. An
    invalid zone name never fails a submit — it logs and falls back."""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            warning(logger, "Invalid hackathon timezone; falling back", tz_name=tz_name)
    for abbr in _SLOT_TZ_ABBR_RE.findall(availability or ""):
        if abbr in _TZ_ABBREVIATION_TO_IANA:
            return ZoneInfo(_TZ_ABBREVIATION_TO_IANA[abbr])
    return ZoneInfo(DEFAULT_EVENT_TIMEZONE)

_CONFIRMATION_HACKATHON_FIELDS = ("title", "location", "timezone", "start_date", "end_date")

_SEASON_RE = re.compile(
    r"\b(spring|summer|fall|autumn|winter)\b(?:\s+[A-Za-z]+){0,2}\s+(20\d{2})\b"
    r"|\b(20\d{2})\s+(spring|summer|fall|autumn|winter)\b",
    re.IGNORECASE,
)


def _ical_escape(text: Any) -> str:
    """RFC 5545 TEXT escaping — a venue like "G Wing, 501 E Tyler Mall, Tempe, AZ"
    carries commas that would otherwise end the property value."""
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _load_hackathon_record(event_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The raw hackathon doc for ``event_id`` (one query), or None when it
    can't be found/read. create_or_update_volunteer loads it ONCE and derives
    both the constraints (required questions, auto-approve) and the
    confirmation-email event summary from it."""
    if not event_id:
        return None
    try:
        docs = get_db().collection('hackathons').where('event_id', '==', event_id).limit(1).stream()
        for doc in docs:
            return doc.to_dict() or {}
    except Exception as e:
        warning(logger, "Failed to load hackathon record", event_id=event_id, exc_info=e)
    return None


def _confirmation_event_from_record(record: Optional[Dict[str, Any]], event_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    event = {k: record.get(k) for k in _CONFIRMATION_HACKATHON_FIELDS}
    event["event_id"] = record.get("event_id") or event_id
    return event


def _hackathon_for_confirmation(event_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The hackathon fields the confirmation email + calendar invites need
    (title, venue, timezone, dates). None when the event can't be found —
    callers fall back to generic copy rather than guessing a location."""
    return _confirmation_event_from_record(_load_hackathon_record(event_id), event_id)


# Review statuses an applicant can never be auto-approved out of — a staff
# decision (or the applicant's own withdrawal) always wins over the switch.
_AUTO_APPROVE_BLOCKED_STATUSES = frozenset({"denied", "withdrew", "no_show"})


def _count_selected_hackers(event_id: str) -> int:
    """How many hackers are on the event roster (isSelected=True). Uses a
    Firestore count() aggregation; falls back to streaming ids when the client
    lacks it (MockFirestore in tests, very old SDKs)."""
    query = (
        get_db().collection('volunteers')
        .where('event_id', '==', event_id)
        .where('volunteer_type', '==', 'hacker')
        .where('isSelected', '==', True)
    )
    try:
        result = query.count(alias='n').get()
        return int(result[0][0].value)
    except Exception:
        try:
            return sum(1 for _ in query.select([]).stream())
        except Exception:
            return sum(1 for _ in query.stream())


def _hacker_roster_decision(volunteer_type: Optional[str], event_id: str,
                            constraints: Optional[Dict[str, Any]]) -> Optional[str]:
    """Auto-approve decision for a hacker submit/update (Nov 2026).

    Returns None unless the event has ``constraints.hacker_auto_approve`` on
    and this is a hacker application; otherwise "approved" while the roster is
    under ``constraints.hacker_capacity`` (None/absent = no limit) and
    "waitlisted" once it is full.

    Read-then-write race: two submits landing at the same moment can both see
    count == capacity - 1 and both get approved, so the roster can exceed the
    cap by a handful. Accepted at walk-in volume (~10 people); admins can take
    anyone off the roster in the Volunteer section.
    """
    if volunteer_type != 'hacker' or not constraints:
        return None
    if constraints.get('hacker_auto_approve') is not True:
        return None
    capacity = constraints.get('hacker_capacity')
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
        return "approved"
    try:
        on_roster = _count_selected_hackers(event_id)
    except Exception as e:
        # Can't tell whether there's room — leave the application pending for
        # a human rather than over- or under-filling the roster.
        warning(logger, "Could not count the hacker roster; skipping auto-approve", event_id=event_id, exc_info=e)
        return None
    return "approved" if on_roster < capacity else "waitlisted"


def _roster_decision_fields(decision: Optional[str]) -> Dict[str, Any]:
    """Fields an auto-approve decision writes onto the volunteer doc."""
    if decision == "approved":
        return {
            'isSelected': True,
            'status': 'approved',
            'auto_approved': True,
            'auto_approved_at': _get_current_timestamp(),
        }
    if decision == "waitlisted":
        return {'status': 'waitlisted', 'auto_approved': False}
    return {}


def _after_hacker_auto_approve(volunteer_id: str, event_id: str, email: str, decision: str) -> None:
    """Bust the admin roster cache and leave a Slack audit trail, mirroring
    update_volunteer_selection. Never allowed to fail the submit."""
    try:
        from services.hackathons_service import get_volunteer_by_event
        get_volunteer_by_event.cache_clear()
    except Exception as e:
        warning(logger, "Failed to clear get_volunteer_by_event cache",
                volunteer_id=volunteer_id, event_id=event_id, exc_info=e)

    if _notifications_disabled():
        return
    try:
        from common.utils.slack import send_slack_audit
        send_slack_audit(
            action="auto_approve_hacker",
            message=(
                f"Hacker {email} was auto-{'approved onto the roster' if decision == 'approved' else 'waitlisted (event at capacity)'} "
                f"for {event_id} (volunteer {volunteer_id})"
            ),
            payload={
                "volunteer_id": volunteer_id,
                "event_id": event_id,
                "decision": decision,
            },
        )
    except Exception as e:
        warning(logger, "Failed to send auto-approve Slack audit",
                volunteer_id=volunteer_id, exc_info=e)


def _calendar_event_name(event: Optional[Dict[str, Any]]) -> str:
    """"Opportunity Hack Fall 2026" from whatever the hackathon title is
    (mirrors the frontend's eventDisplayName so emails and share cards agree)."""
    title = str((event or {}).get("title") or "").strip()
    if not title:
        return "Opportunity Hack Volunteering"
    season = _SEASON_RE.search(title)
    if season:
        word = (season.group(1) or season.group(4)).capitalize()
        year = season.group(2) or season.group(3)
        return f"Opportunity Hack {'Fall' if word == 'Autumn' else word} {year}"
    first = re.split(r"\s*[|:\u2013\u2014]\s*|\s+-\s+", title)[0].strip() or title
    return first if re.search(r"opportunity\s*hack", first, re.IGNORECASE) else f"Opportunity Hack {first}"


def _event_year(event: Optional[Dict[str, Any]]) -> Optional[int]:
    match = re.match(r"(\d{4})-\d{2}-\d{2}", str((event or {}).get("start_date") or ""))
    return int(match.group(1)) if match else None


def _format_event_date_range(start: Any, end: Any) -> str:
    """"2026-11-14" + "2026-11-15" → "Nov 14–15, 2026" (best effort; "" if unparseable)."""
    def parse(value):
        try:
            return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
        except ValueError:
            return None

    s, e = parse(start), parse(end)
    if not s:
        return ""
    if not e or e.date() == s.date():
        return f"{s.strftime('%b')} {s.day}, {s.year}"
    if s.year != e.year:
        return f"{s.strftime('%b')} {s.day}, {s.year} \u2013 {e.strftime('%b')} {e.day}, {e.year}"
    if s.month != e.month:
        return f"{s.strftime('%b')} {s.day} \u2013 {e.strftime('%b')} {e.day}, {e.year}"
    return f"{s.strftime('%b')} {s.day}\u2013{e.day}, {e.year}"


def get_calendar_email_attachment_from_availability(
    availability: str, 
    volunteer_email: str,
    event_name: str = "Opportunity Hack Volunteering", 
    location: Optional[str] = None,
    organizer_email: str = "welcome@ohack.dev",
    year: Optional[int] = None,
    volunteer_type: Optional[str] = None,
    tz_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Parse availability string and generate calendar attachment data for email.
    
    Args:
        availability: String containing availability information
        volunteer_email: Email of the volunteer (for proper ATTENDEE field)
        event_name: Name of the calendar event
        location: Venue for the LOCATION line; omitted when unknown (it used
            to be hardcoded to "Virtual" for every event)
        organizer_email: Email of the event organizer
        year: Year for the event (defaults to current year if not specified)
        tz_name: IANA timezone the slot times are in (the hackathon's
            `timezone`); falls back to the abbreviation found in the slot text.
            Times are written as UTC instants (no TZID — a TZID without a
            VTIMEZONE block is not valid RFC 5545 and Outlook misreads it).
        
    Returns:
        List of dictionaries containing calendar event data for email attachments
    """
    import base64
    logger.info(f"Generating calendar attachments from availability: {availability}")
    from datetime import datetime, timedelta
    import uuid
    import re
    import calendar
    
    if not availability:
        warning(logger, "No availability provided, skipping calendar generation")
        return []

    # Structured slots always start "Weekday, Mon D" — the prefix all 3 parse
    # patterns require. Judge applications send free-text availability, which
    # is expected input here, not an error.
    if not re.search(r'[A-Za-z]+day,\s*[A-Za-z]{3,}\s+\d{1,2}', availability):
        info(logger, "Availability is free text (no structured slots); skipping calendar attachments")
        return []

    # Use current year if not specified
    if not year:
        info(logger, "No year provided, using current year", year=datetime.now().year)
        year = datetime.now().year
    
    # Time slot mapping to actual hours (24-hour format)
    time_slot_mapping = {
        "🌅 Early Morning": {"start": "07:00", "end": "09:00"},
        "☀️ Morning": {"start": "09:00", "end": "12:00"},
        "🏙️ Afternoon": {"start": "13:00", "end": "15:00"},
        "Afternoon": {"start": "13:00", "end": "15:00"},  # Added non-emoji version
        "🌆 Evening": {"start": "16:00", "end": "19:00"},
        "Evening": {"start": "16:00", "end": "19:00"},  # Added non-emoji version
        "🌃 Night": {"start": "20:00", "end": "23:00"},
        "Night": {"start": "20:00", "end": "23:00"},  # Added non-emoji version
        "🌙 Late Night": {"start": "23:00", "end": "02:00"},  # Spans to next day
        "Late Night": {"start": "23:00", "end": "02:00"}  # Added non-emoji version
    }
    
    # The zone the slot times are in (hackathon timezone from the admin
    # Overview section, else the slot's own abbreviation, else the default).
    event_zone = _resolve_event_tz(tz_name, availability)
    
    # Split into individual time slots - need to be smarter about comma splitting
    # The issue is that "Sunday, Oct 12: ☀️ Morning" gets split on the comma between Sunday and Oct
    # We need to split on commas that are followed by a day name pattern
    
    # Use regex to split on commas that are followed by weekday patterns
    time_slots = split_availability_slots(availability)
    
    debug(logger, "Parsed time slots", time_slots=time_slots, slot_count=len(time_slots))
    
    calendar_events = []
    
    for slot in time_slots:
        info(logger, "Processing time slot", slot=slot.strip(), original_slot=slot)
        
        # Parse the date and time slot with improved regex patterns
        # Pattern 1: "Sunday, Oct 12: ☀️ Morning (9am - 12pm PST)"
        pattern1 = r'([A-Za-z]+),\s*([A-Za-z]+)\s+(\d+):\s*(.*?)\s*\(([^)]+)\)'
        match = re.match(pattern1, slot.strip())
        
        if match:
            weekday, month_name, day_str, time_emoji_name, time_range = match.groups()
            info(logger, "SUCCESS: Parsed with pattern 1", 
                 pattern=pattern1,
                 weekday=weekday, 
                 month_name=month_name, 
                 day=day_str, 
                 time_name=time_emoji_name,
                 time_range=time_range)
        else:
            debug(logger, "Pattern 1 did not match", pattern=pattern1, slot=slot.strip())
            
            # Pattern 2: "Sunday, Oct 12-Afternoon" (fallback format)
            pattern2 = r'([A-Za-z]+),\s*([A-Za-z]+)\s+(\d+)[-\s]([A-Za-z\s]+)'
            alt_match = re.match(pattern2, slot.strip())
            if alt_match:
                weekday, month_name, day_str, time_name = alt_match.groups()
                # Map the time name to emoji format
                time_emoji_mapping = {
                    "Afternoon": "🏙️ Afternoon",
                    "Morning": "☀️ Morning", 
                    "Early Morning": "🌅 Early Morning",
                    "Evening": "🌆 Evening",
                    "Night": "🌃 Night",
                    "Late Night": "🌙 Late Night"
                }
                time_emoji_name = time_emoji_mapping.get(time_name.strip(), time_name.strip())
                info(logger, "SUCCESS: Parsed with pattern 2", 
                     pattern=pattern2,
                     weekday=weekday, 
                     month_name=month_name, 
                     day=day_str, 
                     time_name=time_emoji_name,
                     original_time_name=time_name)
            else:
                debug(logger, "Pattern 2 did not match", pattern=pattern2, slot=slot.strip())
                
                # Pattern 3: More flexible - just capture everything after the colon
                pattern3 = r'([A-Za-z]+),\s*([A-Za-z]+)\s+(\d+):\s*(.+)'
                flexible_match = re.match(pattern3, slot.strip())
                if flexible_match:
                    weekday, month_name, day_str, full_time_desc = flexible_match.groups()
                    info(logger, "SUCCESS: Pattern 3 matched", 
                         pattern=pattern3,
                         weekday=weekday, 
                         month_name=month_name, 
                         day=day_str, 
                         full_time_desc=full_time_desc)
                    
                    # Extract just the emoji and time name part (before the parentheses)
                    time_name_pattern = r'(.*?) \('
                    time_name_match = re.match(time_name_pattern, full_time_desc)
                    if time_name_match:
                        time_emoji_name = time_name_match.group(1).strip()
                        info(logger, "SUCCESS: Parsed with pattern 3 (with parentheses)", 
                             weekday=weekday, 
                             month_name=month_name, 
                             day=day_str, 
                             time_name=time_emoji_name,
                             time_name_pattern=time_name_pattern)
                    else:
                        time_emoji_name = full_time_desc.strip()
                        info(logger, "SUCCESS: Parsed with pattern 3 (no parentheses)", 
                             weekday=weekday, 
                             month_name=month_name, 
                             day=day_str, 
                             time_name=time_emoji_name)
                else:
                    warning(logger, "All patterns failed to match slot",
                            slot=slot.strip(),
                            slot_length=len(slot.strip()))
                    continue
        
        try:
            day = int(day_str)
            info(logger, "Successfully parsed day number", day=day, day_str=day_str)
        except ValueError:
            error(logger, "Invalid day number", day_str=day_str, slot=slot)
            continue
        except NameError:
            error(logger, "day_str variable not defined - parsing failed completely", slot=slot)
            continue

        # Convert month name to number
        month_abbr = month_name[:3]  # Take first 3 letters
        try:
            month = list(calendar.month_abbr).index(month_abbr)
            if month == 0:  # Handle case where the abbr isn't found
                raise ValueError("Month abbreviation not found")
        except (ValueError, IndexError):
            # Fallback method using month name
            month_mapping = {
                "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
            }
            month = month_mapping.get(month_abbr, 1) # Default to January if not found
        
        # An explicit range in the slot text wins (volunteer shifts carry their
        # own hours, e.g. "(8:00am - 11:00am MST)"); otherwise fall back to the
        # named mentor blocks.
        slot_times = _parse_explicit_time_range(slot)
        if slot_times:
            info(logger, "Using explicit time range from slot", slot=slot.strip(), slot_times=slot_times)
        else:
            for key, value in time_slot_mapping.items():
                # Handle exact match
                if time_emoji_name == key:
                    slot_times = value
                    break
                # Handle partial match (in case the emoji is missing)
                elif time_emoji_name in key or key in time_emoji_name:
                    slot_times = value
                    break
        
        if not slot_times:
            warning(logger, "Time slot not found in mapping", time_slot=time_emoji_name)
            continue
        
        # Create start and end datetimes
        start_time_str = slot_times["start"]
        end_time_str = slot_times["end"]
        
        try:
            start_hour, start_minute = map(int, start_time_str.split(':'))
            end_hour, end_minute = map(int, end_time_str.split(':'))
        except Exception as e:
            error(logger, "Error parsing time format", exc_info=e, slot_times=slot_times)
            continue
        
        # Create the event date
        try:
            start_date = datetime(year, month, day, start_hour, start_minute)
            
            # Handle late night slots that span to next day
            if time_emoji_name == "🌙 Late Night" or (end_hour < start_hour):
                end_date = datetime(year, month, day, end_hour, end_minute) + timedelta(days=1)
            else:
                end_date = datetime(year, month, day, end_hour, end_minute)
                
            # Generate unique ID for the event
            event_uid = str(uuid.uuid4())
            
            # Wall-clock time in the event zone → UTC instant. zoneinfo picks
            # the right offset per instant, so a DST change mid-event is fine,
            # and every calendar client shows it in the viewer's local time.
            utc = ZoneInfo("UTC")
            start_str = start_date.replace(tzinfo=event_zone).astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
            end_str = end_date.replace(tzinfo=event_zone).astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
            created_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            zone_label = f"{start_date.replace(tzinfo=event_zone).tzname()} ({event_zone.key})"
            debug(logger, "Calendar event time range", start=start_str, end=end_str, zone=event_zone.key)
            summary_text = _ical_escape(f"{event_name} - {time_emoji_name}")
            description_text = _ical_escape(
                f"{(volunteer_type or 'Volunteer').capitalize()} availability time slot for {weekday}, "
                f"{month_name} {day} - {time_emoji_name} (times are {zone_label}). "
                f"Thank you for volunteering with Opportunity Hack!"
            )
            location_line = f"LOCATION:{_ical_escape(location)}\n" if location else ""
            
            # Clean up emoji names for filenames
            clean_time_name = re.sub(r'[^\w\s-]', '', time_emoji_name).strip().replace(' ', '_')
            
            # Create iCalendar content with proper formatting for Google Calendar compatibility
            ical_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Opportunity Hack//Calendar Event//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{event_uid}@ohack.dev
DTSTART:{start_str}
DTEND:{end_str}
DTSTAMP:{created_str}
CREATED:{created_str}
LAST-MODIFIED:{created_str}
SUMMARY:{summary_text}
DESCRIPTION:{description_text}
{location_line}ORGANIZER:MAILTO:{organizer_email}
ATTENDEE:MAILTO:{volunteer_email}
STATUS:CONFIRMED
TRANSP:OPAQUE
SEQUENCE:0
CLASS:PUBLIC
BEGIN:VALARM
TRIGGER:-PT15M
ACTION:DISPLAY
DESCRIPTION:Reminder: {event_name} in 15 minutes
END:VALARM
END:VEVENT
END:VCALENDAR"""
            
            debug(logger, "Generated iCalendar content", date=f"{weekday}, {month_name} {day}", time_slot=time_emoji_name)
            
            # Don't base64 encode - Resend expects raw content for attachments
            # Create attachment object formatted for Resend
            calendar_events.append({
                "filename": f"OHack_{volunteer_type}_{month_name}_{day}_{clean_time_name}.ics",
                "content": ical_content,  # Use raw string content, not base64
                "type": "text/calendar",
                "disposition": "attachment"
            })
            info(logger, "Generated calendar event", date=f"{weekday}, {month_name} {day}", time_slot=time_emoji_name)
                
        except ValueError as e:
            exception(logger, "Error parsing date", exc_info=e, date_info=f"{month_name} {day}, {year}")
            continue
    
    return calendar_events

def create_or_update_volunteer(
    user_id: str,
    event_id: str,
    email: str,
    volunteer_data: Dict[str, Any],
    created_by: Optional[str] = None,
    recaptcha_token: Optional[str] = None
) -> Dict[str, Any]:
    # Process availability data
    availability = volunteer_data.get('availability', '')
    available_days = volunteer_data.get('availableDays', [])
    
    # If availability is not set but availableDays is, construct availability string
    if not availability and available_days and isinstance(available_days, list):
        # Convert availableDays format to the expected availability format
        converted_slots = []
        for day in available_days:
            # Parse the day string (e.g., "Sunday, Oct 12-Afternoon")
            parts = day.split('-')
            if len(parts) >= 2:
                date_part = parts[0].strip()
                time_part = parts[1].strip()
                
                # Map time part to emoji format
                time_emoji_mapping = {
                    "Afternoon": "🏙️ Afternoon (1pm - 3pm PST)",
                    "Morning": "☀️ Morning (9am - 12pm PST)",
                    "Early Morning": "🌅 Early Morning (7am - 9am PST)",
                    "Evening": "🌆 Evening (4pm - 7pm PST)",
                    "Night": "🌃 Night (8pm - 11pm PST)",
                    "Late Night": "🌙 Late Night (11pm - 2am PST)"
                }
                
                emoji_time = time_emoji_mapping.get(time_part, f"{time_part} (PST)")
                converted_slots.append(f"{date_part}: {emoji_time}")
        
        if converted_slots:
            availability = ", ".join(converted_slots)
            volunteer_data['availability'] = availability
            info(logger, "Converted availableDays to availability format", availability=availability)
    """
    Create or update a volunteer record.
    
    Args:
        user_id: The authenticated user ID        
        event_id: The event ID
        volunteer_data: The full volunteer data
        created_by: User who created the record (optional)
        recaptcha_token: Google reCAPTCHA token for verification when user is not authenticated
        
    Returns:
        The created or updated volunteer record
    """
    # If no user_id and recaptcha_token is provided, verify recaptcha
    if not user_id and recaptcha_token:
        if not verify_recaptcha(recaptcha_token):
            warning(logger, "reCAPTCHA verification failed", email=email)
            return {"error": "reCAPTCHA verification failed"}

    # ONE hackathon lookup serves the required-questions check, the hacker
    # auto-approve decision and the confirmation email/.ics context below.
    hackathon_record = _load_hackathon_record(event_id)
    constraints = (hackathon_record or {}).get('constraints') or {}

    # Validate required question answers for hacker applications
    if volunteer_data.get('volunteer_type') == 'hacker':
        submitted_answers = volunteer_data.get('requiredQuestionAnswers', [])
        if submitted_answers:
            try:
                if hackathon_record:
                    questions = (constraints.get('hacker_required_questions') or {}).get('questions', [])
                    if questions:
                        if len(submitted_answers) != len(questions):
                            warning(logger, "Required question answer count mismatch", email=email, event_id=event_id)
                            return {"error": "Required question answers do not match the expected number of questions"}
                        for i, q in enumerate(questions):
                            if i >= len(submitted_answers) or submitted_answers[i] != q.get('required_answer'):
                                warning(logger, "Required question answer incorrect", email=email, event_id=event_id, question_index=i)
                                return {"error": q.get('error', 'You do not meet the eligibility requirements for this event.')}
            except Exception as e:
                exception(logger, "Error validating required questions", exc_info=e, email=email, event_id=event_id)

    db = get_db()
    volunteer_type = volunteer_data.get('volunteer_type')

    # Applicants may not write staff-owned fields. Several application forms ship
    # a stale `isSelected: false` from their initial form state, which used to
    # silently un-approve the applicant on every edit. Stripping here (before the
    # create/update branch) covers both paths: on update, set(merge=True) omits
    # the keys so the stored values survive; on create, it stops a crafted
    # payload from overriding the isSelected=False seed to self-approve.
    volunteer_data = {
        k: v for k, v in volunteer_data.items()
        if k not in STAFF_OWNED_VOLUNTEER_FIELDS
    }

    # Check if volunteer already exists. Use the SAME 3-way identity resolver
    # as the GET route — matching by propel UUID only made a user whose doc was
    # stored under another identity shape (email / OAuth id) fall into the
    # create branch on edit, spawning a duplicate isSelected=False doc.
    existing = find_volunteer_by_caller_identity(user_id, event_id, volunteer_type)

    # Title / venue / timezone / dates for the confirmation email and its .ics
    # invites — derived from the record loaded above.
    event_ctx = _confirmation_event_from_record(hackathon_record, event_id)
    roster_outcome = None
    
    if existing:
        # Update existing record
        volunteer_id = existing.get('id')
        volunteer_ref = db.collection('volunteers').document(volunteer_id)
        
        # Prepare complete update data including all volunteer_data fields
        update_data = {**volunteer_data}
        update_data['updated_by'] = user_id
        update_data['updated_timestamp'] = _get_current_timestamp()
        
        # Preserve original creation metadata
        update_data['id'] = volunteer_id
        update_data['user_id'] = existing.get('user_id', user_id)
        update_data['event_id'] = existing.get('event_id', event_id)
        update_data['email'] = existing.get('email', email)
        update_data['created_by'] = existing.get('created_by')
        update_data['created_timestamp'] = existing.get('created_timestamp')
        update_data['timestamp'] = existing.get('timestamp')
        update_data['status'] = existing.get('status', 'active')
        
        # isSelected is staff-owned (stripped from volunteer_data above) — carry
        # the stored value forward so docs predating the field still get one.
        update_data['isSelected'] = existing.get('isSelected', False)

        # Hacker auto-approve (Nov 2026): an applicant still waiting on review
        # who updates while the switch is on goes onto the roster too. Never
        # un-approves, and never overrides a denied/withdrew/no_show decision.
        # At capacity the stored status is left alone (they stay pending).
        existing_status = str(existing.get('status') or '').strip().lower()
        if not existing.get('isSelected') and existing_status not in _AUTO_APPROVE_BLOCKED_STATUSES:
            if _hacker_roster_decision(volunteer_type, event_id, constraints) == "approved":
                roster_outcome = "approved"
                update_data.update(_roster_decision_fields("approved"))

        # Log volunteer_data
        logger.debug(f"volunteer_data: {volunteer_data}")
        logger.debug(f"Updating volunteer record {volunteer_id} with complete data including new fields {update_data}")
        
        # Use set with merge=True to ensure all fields are updated, including new ones
        volunteer_ref.set(update_data, merge=True)

        calendar_attachments = get_calendar_email_attachment_from_availability(
            volunteer_data.get('availability', ''),
            email,  # Pass volunteer email for proper ATTENDEE field
            event_name=_calendar_event_name(event_ctx),
            location=(event_ctx or {}).get('location') or None,
            organizer_email="welcome@ohack.dev",
            year=_event_year(event_ctx),
            volunteer_type=volunteer_type,
            tz_name=(event_ctx or {}).get('timezone') or None,
        )
        
        # Clear all related caches
        _clear_volunteer_caches(user_id, email, event_id, volunteer_type)
        if roster_outcome:
            _after_hacker_auto_approve(volunteer_id, event_id, email, roster_outcome)
        
        # Send admin notification about the update
        try:
            send_admin_notification_email({**existing, **update_data}, is_update=True)
            send_slack_volunteer_notification({**existing, **update_data}, is_update=True)
            
            # Extract name properly from volunteer data
            name = volunteer_data.get('name', '')
            first_name = volunteer_data.get('firstName', '')
            last_name = volunteer_data.get('lastName', '')
            
            # If name contains both first and last name, try to split it
            if name and not (first_name and last_name):
                name_parts = name.split(' ', 1)
                if len(name_parts) > 1:
                    first_name = name_parts[0]
                    last_name = name_parts[1]
                else:
                    first_name = name
            
            # Check if we have calendar attachments
            if calendar_attachments and len(calendar_attachments) > 0:
                info(logger, "Sending email with calendar attachments", email=email, attachment_count=len(calendar_attachments))
            else:
                info(logger, "No calendar attachments generated from availability data", email=email)
                
            send_volunteer_confirmation_email(first_name, last_name, email, volunteer_type, calendar_attachments, event_id, volunteer_id=volunteer_id,
                                              shifts=split_availability_slots(volunteer_data.get('availability', '')),
                                              event=event_ctx, roster_outcome=roster_outcome)

            info(logger, "Sent notifications about updated volunteer", email=email)
        except Exception as e:
            exception(logger, "Failed to send notifications for updated volunteer", exc_info=e, email=email)
        
        return {**existing, **update_data}
    else:
        # Create new record
        volunteer_id = _generate_volunteer_id()
        
        # Prepare volunteer document
        volunteer_doc = {
            'id': volunteer_id,
            'user_id': user_id,
            'event_id': event_id,
            'timestamp': _get_current_timestamp(),
            'email': email,
            'isSelected': False,
            'created_by': created_by or user_id,
            'created_timestamp': _get_current_timestamp(),
            'updated_by': created_by or user_id,
            'updated_timestamp': _get_current_timestamp(),
        }
        
        # Add volunteer data
        volunteer_doc.update(volunteer_data)

        # Hacker auto-approve (Nov 2026) — see _hacker_roster_decision.
        roster_outcome = _hacker_roster_decision(volunteer_type, event_id, constraints)
        volunteer_doc.update(_roster_decision_fields(roster_outcome))
        
        # Try to get Slack user ID
        try:
            slack_info = get_slack_user_by_email(email)
            if slack_info and 'id' in slack_info:
                volunteer_doc['slack_user_id'] = slack_info['id']
        except Exception as e:
            warning(logger, "Could not get slack user", exc_info=e, email=email)
        
        # Save to database
        db.collection('volunteers').document(volunteer_id).set(volunteer_doc)
        
        # Clear all related caches 
        _clear_volunteer_caches(user_id, email, event_id, volunteer_type)
        if roster_outcome:
            _after_hacker_auto_approve(volunteer_id, event_id, email, roster_outcome)

        calendar_attachments = get_calendar_email_attachment_from_availability(
            volunteer_data.get('availability', ''),
            email,  # Pass volunteer email for proper ATTENDEE field
            event_name=_calendar_event_name(event_ctx),
            location=(event_ctx or {}).get('location') or None,
            organizer_email="welcome@ohack.dev",
            year=_event_year(event_ctx),
            volunteer_type=volunteer_type,
            tz_name=(event_ctx or {}).get('timezone') or None,
        )
        
        # Send confirmation email to the volunteer
        try:
            # Extract name properly from volunteer data
            name = volunteer_doc.get('name', '')
            first_name = volunteer_doc.get('firstName', '')
            last_name = volunteer_doc.get('lastName', '')
            
            # If name contains both first and last name, try to split it
            if name and not (first_name and last_name):
                name_parts = name.split(' ', 1)
                if len(name_parts) > 1:
                    first_name = name_parts[0]
                    last_name = name_parts[1]
                else:
                    first_name = name
            
            # Check if we have calendar attachments
            if calendar_attachments and len(calendar_attachments) > 0:
                info(logger, "Sending email with calendar attachments", email=email, attachment_count=len(calendar_attachments))
            else:
                info(logger, "No calendar attachments generated from availability data", email=email)
                
            send_volunteer_confirmation_email(first_name, last_name, email, volunteer_type, calendar_attachments, event_id, volunteer_id=volunteer_id,
                                              shifts=split_availability_slots(volunteer_data.get('availability', '')),
                                              event=event_ctx, roster_outcome=roster_outcome)
            send_admin_notification_email(volunteer_doc)
            send_slack_volunteer_notification(volunteer_doc)
            info(logger, "Sent notifications for new volunteer", email=email)
        except Exception as e:
            exception(logger, "Failed to send notifications for new volunteer", exc_info=e, email=email)
        
        return volunteer_doc

def update_volunteer_selection(volunteer_id: str, selected: bool, updated_by: str) -> Dict[str, Any]:
    """
    Single admin writer for isSelected (event roster).

    Backs ``POST /api/admin/volunteer/<volunteer_id>/select``. Status
    (``status``) is a separate field written through the hackathon PATCH route
    (``update_hackathon_volunteers``); that route must not be used for
    isSelected, and this function writes nothing but the flag + audit stamps.

    Args:
        volunteer_id: The volunteer ID
        selected: The selection status (True/False)
        updated_by: User who made the update

    Returns:
        The updated volunteer record, or None when the doc doesn't exist
    """
    db = get_db()
    volunteer_ref = db.collection('volunteers').document(volunteer_id)
    volunteer_data = volunteer_ref.get().to_dict()
    
    if not volunteer_data:
        return None
    
    # Update only isSelected field
    update_data = {
        'isSelected': selected,
        'updated_by': updated_by,
        'updated_timestamp': _get_current_timestamp()
    }
    
    volunteer_ref.update(update_data)

    # Clear all related caches
    user_id = volunteer_data.get('user_id')
    email = volunteer_data.get('email')
    event_id = volunteer_data.get('event_id')
    volunteer_type = volunteer_data.get('volunteer_type')
    _clear_volunteer_caches(user_id, email, event_id, volunteer_type)

    # The admin roster list (GET /api/messages/admin/hackathon/<event>/<type>)
    # is served from hackathons_service.get_volunteer_by_event — an in-process
    # cachetools TTLCache the redis pattern-clear above never reaches. Without
    # this it kept serving the pre-toggle isSelected for the rest of the TTL.
    # Lazy import: hackathons_service is heavy and doesn't import this module.
    try:
        from services.hackathons_service import get_volunteer_by_event
        get_volunteer_by_event.cache_clear()
    except Exception as e:
        warning(logger, "Failed to clear get_volunteer_by_event cache",
                volunteer_id=volunteer_id, event_id=event_id, exc_info=e)

    # Slack audit — parity with update_hackathon_volunteers (the PATCH route).
    # Gated like every other outbound notification in this service so tests
    # never post to Slack; never allowed to fail the write.
    if not _notifications_disabled():
        try:
            from common.utils.slack import send_slack_audit
            send_slack_audit(
                action="update_volunteer_selection",
                message=(
                    f"{updated_by} set isSelected={selected} for {volunteer_type} "
                    f"volunteer {volunteer_id} (event_id={event_id})"
                ),
                payload={
                    "volunteer_id": volunteer_id,
                    "event_id": event_id,
                    "volunteer_type": volunteer_type,
                    "isSelected": selected,
                    "updated_by": updated_by,
                },
            )
        except Exception as e:
            warning(logger, "Failed to send selection Slack audit",
                    volunteer_id=volunteer_id, exc_info=e)

    return {**volunteer_data, **update_data}


def refund_hacker_deposit(
    volunteer_id: str,
    admin_user_id: str,
    override: bool = False,
) -> Dict[str, Any]:
    """Issue a Stripe refund for a hacker's deposit and update the volunteer doc.

    Ordering matters: Stripe is the irreversible step, so we call Stripe FIRST and
    then update Firestore. If Firestore fails AFTER a successful refund, the
    refund_id is in the Stripe dashboard and the failure is logged for human
    reconciliation — we do NOT try to undo the refund.

    Raises ValueError for caller-facing validation errors (400-class), and
    RuntimeError when Stripe rejects the refund or the secret key is missing
    (502-class).
    """
    db = get_db()
    volunteer_ref = db.collection('volunteers').document(volunteer_id)
    snapshot = volunteer_ref.get()
    if not snapshot.exists:
        raise ValueError("Volunteer not found")
    volunteer_data = snapshot.to_dict()

    if volunteer_data.get('volunteer_type') != 'hacker':
        raise ValueError("Only hacker deposits can be refunded via this endpoint")

    payment_intent_id = volunteer_data.get('stripe_payment_intent_id')
    if not payment_intent_id:
        raise ValueError("This hacker has no recorded deposit payment")

    current_status = volunteer_data.get('deposit_status') or 'paid'
    if current_status == 'refunded':
        raise ValueError("Deposit has already been refunded")

    disposition = volunteer_data.get('deposit_disposition')
    if disposition == 'donate' and not override:
        raise ValueError(
            "This hacker chose to donate. Pass override=true to refund anyway."
        )

    stripe_api_key = os.environ.get('STRIPE_SECRET_KEY')
    if not stripe_api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured on this server")
    stripe.api_key = stripe_api_key

    now = _get_current_timestamp()

    try:
        refund = stripe.Refund.create(
            payment_intent=payment_intent_id,
            metadata={
                'volunteer_id': volunteer_id,
                'event_id': volunteer_data.get('event_id', ''),
                'admin_user_id': admin_user_id,
                'override': 'true' if override else 'false',
            },
        )
    except stripe.error.StripeError as e:
        # Surface the failure on the doc so the admin sees it without re-grepping logs.
        msg = getattr(e, 'user_message', None) or str(e)
        fail_update = {
            'deposit_status': 'refund_failed',
            'deposit_refund_status_msg': msg,
            'updated_by': admin_user_id,
            'updated_timestamp': now,
        }
        try:
            volunteer_ref.update(fail_update)
        except Exception as inner:
            exception(
                logger,
                "Failed to persist refund_failed status",
                exc_info=inner,
                volunteer_id=volunteer_id,
            )
        exception(
            logger,
            "Stripe refund failed",
            exc_info=e,
            volunteer_id=volunteer_id,
            payment_intent_id=payment_intent_id,
        )
        raise RuntimeError(f"Stripe refund failed: {msg}")

    update_data = {
        'deposit_status': 'refunded',
        'deposit_refund_id': refund.id,
        'deposit_refund_amount_cents': refund.amount,
        'deposit_refunded_at': now,
        'deposit_refunded_by': admin_user_id,
        'deposit_refund_status_msg': None,
        'updated_by': admin_user_id,
        'updated_timestamp': now,
    }
    volunteer_ref.update(update_data)

    user_id = volunteer_data.get('user_id')
    email = volunteer_data.get('email')
    event_id = volunteer_data.get('event_id')
    volunteer_type = volunteer_data.get('volunteer_type')
    _clear_volunteer_caches(user_id, email, event_id, volunteer_type)

    info(
        logger,
        "Hacker deposit refunded",
        volunteer_id=volunteer_id,
        refund_id=refund.id,
        amount_cents=refund.amount,
        admin_user_id=admin_user_id,
        override=override,
    )

    return {**volunteer_data, **update_data, 'id': volunteer_id}


def bulk_refund_eligible_hacker_deposits(
    event_id: str,
    admin_user_id: str,
) -> Dict[str, Any]:
    """Refund every hacker for this event whose deposit is paid + disposition=refund.

    Excludes donate-disposition (override is a per-row decision by design) and
    refund_failed rows (those likely need human triage; admin can retry per-row).
    Per-row failures are collected, not fatal — one bad row does not abort the
    rest of the batch.
    """
    db = get_db()
    query = (
        db.collection('volunteers')
        .where(filter=FieldFilter('event_id', '==', event_id))
        .where(filter=FieldFilter('volunteer_type', '==', 'hacker'))
        .where(filter=FieldFilter('deposit_status', '==', 'paid'))
        .where(filter=FieldFilter('deposit_disposition', '==', 'refund'))
    )

    refunded = []
    failed = []
    total_amount_cents = 0

    for snap in query.stream():
        vol_id = snap.id
        doc = snap.to_dict()
        display_name = doc.get('name') or doc.get('email') or vol_id
        try:
            updated = refund_hacker_deposit(
                volunteer_id=vol_id,
                admin_user_id=admin_user_id,
                override=False,
            )
            amount = updated.get('deposit_refund_amount_cents') or 0
            refunded.append({
                'id': vol_id,
                'name': display_name,
                'email': doc.get('email'),
                'amount_cents': amount,
            })
            total_amount_cents += amount
        except Exception as e:  # noqa: BLE001 — surface every error per-row
            failed.append({
                'id': vol_id,
                'name': display_name,
                'email': doc.get('email'),
                'error': str(e),
            })
            exception(
                logger,
                "Bulk refund failed for volunteer",
                exc_info=e,
                volunteer_id=vol_id,
            )

    info(
        logger,
        "Bulk refund batch complete",
        event_id=event_id,
        admin_user_id=admin_user_id,
        refunded_count=len(refunded),
        failed_count=len(failed),
        total_amount_cents=total_amount_cents,
    )

    return {
        'refunded': refunded,
        'failed': failed,
        'total_amount_cents': total_amount_cents,
    }


def _find_hacker_by_email_and_event(email: str, event_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return (doc_id, doc_dict) for the most-recent hacker app matching email+event, or None."""
    if not email or not event_id:
        return None
    db = get_db()
    query = (
        db.collection('volunteers')
        .where(filter=FieldFilter('event_id', '==', event_id))
        .where(filter=FieldFilter('volunteer_type', '==', 'hacker'))
        .where(filter=FieldFilter('email', '==', email))
    )
    # Pick the most recently updated one in Python (avoids requiring a composite index).
    best_snap = None
    best_ts = ''
    for snap in query.stream():
        data = snap.to_dict() or {}
        ts = data.get('updated_timestamp') or data.get('timestamp') or ''
        if ts >= best_ts:
            best_snap = snap
            best_ts = ts
    if not best_snap:
        return None
    return best_snap.id, best_snap.to_dict()


def handle_stripe_hacker_deposit_event(
    payload_bytes: bytes,
    sig_header: Optional[str],
) -> Tuple[bool, str]:
    """Verify and process a Stripe webhook event for the hacker-deposit flow.

    Returns (ok, message). Raises ValueError for bad signatures (caller should
    return 400) and RuntimeError for config issues (caller should return 500).
    All other event-specific issues are returned as ok=True with a descriptive
    message — Stripe needs a 2xx response or it will retry, and the only thing
    a retry helps with is transient errors, not 'we ignored an event by design'.
    """
    # Renamed from STRIPE_WEBHOOK_SECRET so the hacker-deposit endpoint can't
    # collide with the store webhook's secret on the frontend. Each Stripe
    # webhook endpoint in the dashboard has its own signing secret.
    webhook_secret = os.environ.get('STRIPE_HACKER_DEPOSIT_WEBHOOK_SECRET')
    if not webhook_secret:
        raise RuntimeError(
            "STRIPE_HACKER_DEPOSIT_WEBHOOK_SECRET is not configured on this server"
        )
    if not sig_header:
        raise ValueError("Missing Stripe-Signature header")

    stripe_api_key = os.environ.get('STRIPE_SECRET_KEY')
    if stripe_api_key:
        stripe.api_key = stripe_api_key

    try:
        event = stripe.Webhook.construct_event(
            payload=payload_bytes,
            sig_header=sig_header,
            secret=webhook_secret,
        )
    except ValueError as e:
        raise ValueError(f"Invalid payload: {e}")
    except stripe.error.SignatureVerificationError as e:
        raise ValueError(f"Signature verification failed: {e}")

    event_type = event.get('type')
    obj = event.get('data', {}).get('object', {}) or {}

    if event_type == 'checkout.session.completed':
        return _handle_checkout_session_completed(obj)
    if event_type == 'charge.refunded':
        return _handle_charge_refunded(obj)

    return True, f"Ignored event type {event_type}"


def _expected_hacker_deposit_cents(event_id: str) -> Optional[int]:
    """The event's constraints.hacker_deposit.default_amount_cents, or None
    (missing config / lookup error — callers fail open)."""
    try:
        from services.hackathons_service import get_single_hackathon_event
        event = get_single_hackathon_event(event_id) or {}
        cents = ((event.get('constraints') or {}).get('hacker_deposit') or {}).get('default_amount_cents')
        if isinstance(cents, bool) or not isinstance(cents, int):
            return None
        return cents
    except Exception as e:
        warning(logger, "Could not resolve hacker deposit amount", event_id=event_id, error=str(e))
        return None


def _handle_checkout_session_completed(session: Dict[str, Any]) -> Tuple[bool, str]:
    """Self-heal the volunteer doc when Stripe confirms a checkout payment.

    The volunteer doc is created when the hacker clicks Submit on the
    application form AFTER returning from Stripe. The webhook may arrive
    before, during, or after that submit. When it arrives BEFORE, there's
    nothing to update and we no-op — the form submit handler still records
    the deposit from its own session-status read.

    When the webhook arrives AFTER, we update missing deposit fields. We
    never regress state: a doc already in 'refunded' stays refunded.
    """
    metadata = session.get('metadata') or {}
    if metadata.get('kind') != 'hacker_deposit':
        return True, "Ignored: not a hacker_deposit checkout"

    event_id = metadata.get('event_id')
    email = (
        metadata.get('hacker_email')
        or session.get('customer_email')
        or (session.get('customer_details') or {}).get('email')
    )
    payment_intent_id = session.get('payment_intent')
    amount_total = session.get('amount_total')
    disposition = metadata.get('disposition') or 'refund'

    if not (event_id and email and payment_intent_id):
        return True, (
            f"Skipped: missing fields (event_id={event_id}, "
            f"email={'set' if email else 'none'}, "
            f"payment_intent={'set' if payment_intent_id else 'none'})"
        )

    match = _find_hacker_by_email_and_event(email, event_id)
    if not match:
        # Application doc not yet created. The form-submit path will record
        # the deposit when the hacker hits Submit; no action needed here.
        warning(
            logger,
            "Webhook: no volunteer doc yet — deposit recorded only in Stripe for now",
            event_id=event_id,
            payment_intent_id=payment_intent_id,
        )
        return True, "No matching volunteer doc (webhook beat submit)"

    vol_id, doc = match
    if doc.get('deposit_status') == 'refunded':
        return True, "Already refunded — not regressing"

    # Best-effort amount check: never mark a session that paid less than the
    # event's configured deposit as 'paid'. Runs BEFORE the idempotent
    # already-paid shortcut so an underpaid session can't ride on a doc the
    # client-side submit already stamped 'paid'. Unknown config fails open.
    expected_cents = _expected_hacker_deposit_cents(event_id)
    if (
        isinstance(expected_cents, int)
        and isinstance(amount_total, int)
        and amount_total < expected_cents
    ):
        db = get_db()
        db.collection('volunteers').document(vol_id).update({
            'deposit_status': 'underpaid',
            'deposit_amount_cents': amount_total,
            'stripe_payment_intent_id': payment_intent_id,
            'updated_timestamp': _get_current_timestamp(),
        })
        _clear_volunteer_caches(doc.get('user_id'), email, event_id, 'hacker')
        try:
            from common.utils.slack import send_slack_audit
            send_slack_audit(
                action="hacker_deposit_underpaid",
                message=(
                    f"Hacker deposit underpaid for volunteer {vol_id}: "
                    f"{amount_total} < {expected_cents} cents"
                ),
                payload={
                    'volunteer_id': vol_id,
                    'event_id': event_id,
                    'payment_intent_id': payment_intent_id,
                    'amount_total': amount_total,
                    'expected_cents': expected_cents,
                },
            )
        except Exception as e:  # audit is best-effort
            warning(logger, "Underpaid deposit audit failed", error=str(e))
        warning(
            logger,
            "Webhook: underpaid hacker deposit — not marking paid",
            volunteer_id=vol_id,
            payment_intent_id=payment_intent_id,
        )
        return True, f"Underpaid deposit recorded for {vol_id}"

    if (
        doc.get('deposit_status') == 'paid'
        and doc.get('stripe_payment_intent_id') == payment_intent_id
    ):
        return True, "Already paid (idempotent no-op)"

    db = get_db()
    update = {
        'deposit_status': 'paid',
        'stripe_payment_intent_id': payment_intent_id,
        'deposit_amount_cents': amount_total,
        # Preserve a previously-stored disposition; only fill in if missing.
        'deposit_disposition': doc.get('deposit_disposition') or disposition,
        'updated_timestamp': _get_current_timestamp(),
    }
    db.collection('volunteers').document(vol_id).update(update)
    _clear_volunteer_caches(
        doc.get('user_id'), email, event_id, 'hacker',
    )
    info(
        logger,
        "Webhook self-healed paid deposit",
        volunteer_id=vol_id,
        payment_intent_id=payment_intent_id,
    )
    return True, f"Self-healed volunteer {vol_id}"


def _handle_charge_refunded(charge: Dict[str, Any]) -> Tuple[bool, str]:
    """Confirm a refund settled. Only acts on refunds we created (volunteer_id metadata)."""
    refunds_data = (charge.get('refunds') or {}).get('data') or []
    our_refund = None
    for r in refunds_data:
        rmeta = r.get('metadata') or {}
        if rmeta.get('volunteer_id'):
            our_refund = r
            break
    if not our_refund:
        return True, "Ignored charge.refunded — no hacker_deposit refund metadata"

    volunteer_id = our_refund['metadata']['volunteer_id']
    refund_id = our_refund['id']
    refund_amount = our_refund.get('amount')

    db = get_db()
    doc_ref = db.collection('volunteers').document(volunteer_id)
    snap = doc_ref.get()
    if not snap.exists:
        return True, f"No volunteer doc for {volunteer_id}"
    doc = snap.to_dict()
    if (
        doc.get('deposit_status') == 'refunded'
        and doc.get('deposit_refund_id') == refund_id
    ):
        return True, "Already recorded as refunded (idempotent)"

    update = {
        'deposit_status': 'refunded',
        'deposit_refund_id': refund_id,
        'deposit_refund_amount_cents': refund_amount,
        'deposit_refund_status_msg': None,
        'updated_timestamp': _get_current_timestamp(),
    }
    if not doc.get('deposit_refunded_at'):
        update['deposit_refunded_at'] = _get_current_timestamp()
    if not doc.get('deposit_refunded_by'):
        update['deposit_refunded_by'] = (
            our_refund['metadata'].get('admin_user_id') or 'stripe_webhook'
        )

    doc_ref.update(update)
    _clear_volunteer_caches(
        doc.get('user_id'), doc.get('email'), doc.get('event_id'), 'hacker',
    )
    info(
        logger,
        "Webhook confirmed refund settled",
        volunteer_id=volunteer_id,
        refund_id=refund_id,
    )
    return True, f"Recorded refund for {volunteer_id}"


# Allowlist for the hacker directory (GET /api/hacker/applications/<event_id>).
# Every key frontend findteam.js reads, plus teamCode (hacker form "Find your
# team" picker) and isSelected (api/peer_votes/peer_votes_service.py reads it).
# Anything else on a hacker application (phone, deposit bookkeeping, sent-email
# logs, free-text notes, ...) must never ship through this route.
HACKER_DIRECTORY_FIELDS = frozenset({
    "id", "user_id", "name", "github", "slack_user_id", "pronouns",
    "linkedin", "linkedinProfile", "photoUrl", "portfolio",
    "experienceLevel", "participationCount", "participantType",
    "schoolOrganization", "willContinue", "inPerson", "isInPerson",
    "shortBio", "bio", "socialCauses", "primaryRoles", "skills",
    "teamNeededSkills", "teamMatchingPreferences",
    "teamMatchingPreferredCauses", "teamMatchingPreferredSkills",
    "teamMatchingPreferredSize", "teamStatus", "teamCode", "isSelected",
})


def get_all_hackers_by_event_id(event_id: str) -> List[Dict[str, Any]]:
    """
    Get all hackers for a specific event ID.

    Args:
        event_id: The event ID

    Returns:
        List of hacker records
    """
    db = get_db()
    hackers = db.collection('volunteers').where('event_id', '==', event_id).where('volunteer_type', '==', 'hacker').stream()
    hacker_dict = [hacker.to_dict() for hacker in hackers] if hackers else []

    # Project to the directory allowlist (was a 4-key denylist that leaked
    # phone, deposit fields, sent_emails, ...).
    hacker_dict = [
        {k: v for k, v in hacker.items() if k in HACKER_DIRECTORY_FIELDS}
        for hacker in hacker_dict
    ]
    return hacker_dict


# Roles whose attendance is gated on admin selection + check-in. Hackers are not
# selection-gated; sponsors are not part of the user-facing hackathon history.
SELECTABLE_ROLE_TYPES = {"mentor", "judge", "volunteer"}
ATTENDANCE_ROLE_LABELS = {
    "hacker": "Hacker",
    "mentor": "Mentor",
    "judge": "Judge",
    "volunteer": "Volunteer",
}


def _did_volunteer_attend(vol: Dict[str, Any]) -> bool:
    """Apply the attendance filter for a single volunteer record.

    - hacker: attended iff the application exists
    - mentor / judge / volunteer: attended iff isSelected AND a check-in exists
    - everything else (sponsor, unknown): not counted as attendance
    """
    vtype = (vol.get('volunteer_type') or '').lower()
    if vtype == 'hacker':
        return True
    if vtype in SELECTABLE_ROLE_TYPES:
        return bool(vol.get('isSelected')) and bool(vol.get('checkInTime'))
    return False


def _enrich_with_hackathon(db, event_id: str) -> Optional[Dict[str, Any]]:
    """Look up the hackathon doc for an event_id and trim it to display-friendly fields."""
    try:
        docs = db.collection('hackathons').where('event_id', '==', event_id).limit(1).stream()
    except Exception as e:
        warning(logger, "Failed to query hackathon for event_id", event_id=event_id, exc_info=e)
        return None

    for doc in docs:
        rec = doc.to_dict() or {}
        rec['id'] = doc.id

        # Resolve nonprofit Firestore refs into plain dicts (best-effort)
        nonprofit_refs = rec.get('nonprofits') or []
        resolved = []
        if nonprofit_refs:
            try:
                npo_docs = db.get_all(nonprofit_refs)
                for npo_doc in npo_docs:
                    if not getattr(npo_doc, 'exists', True):
                        continue
                    npo = npo_doc.to_dict() or {}
                    npo['id'] = getattr(npo_doc, 'id', npo.get('id'))
                    npo.pop('problem_statements', None)
                    resolved.append(npo)
            except Exception as e:
                warning(logger, "Failed to resolve nonprofit refs", event_id=event_id, exc_info=e)
        rec['nonprofits'] = resolved

        # Strip heavy fields the profile UI doesn't render.
        for k in ('teams', 'donation_current', 'donation_goals'):
            rec.pop(k, None)
        return rec
    return None


def get_user_hackathon_attendance(
    user_id: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the list of hackathons the user actually attended.

    Sourced from the unified `volunteers` collection. We look up by `user_id`
    and (as a fallback) `email` so we catch records created via either path.
    Multiple roles for the same event collapse into one entry with a `roles`
    list (e.g. a user who hacked AND mentored gets one card with two chips).

    Returns shape consumed by both public and private profile views:
        [{
            "event_id": str,
            "title": str,
            "start_date": str,
            "end_date": str,
            "location": str,
            "image_url": str,
            "links": list,
            "nonprofits": list,
            "roles": list[str],
        }, ...]
    """
    if not user_id and not email:
        return []

    db = get_db()
    seen_ids = set()
    volunteer_docs: List[Dict[str, Any]] = []

    def _collect(query):
        try:
            for snap in query.stream():
                if snap.id in seen_ids:
                    continue
                seen_ids.add(snap.id)
                data = snap.to_dict() or {}
                data['_doc_id'] = snap.id
                volunteer_docs.append(data)
        except Exception as e:
            warning(logger, "volunteers query failed", exc_info=e)

    if user_id:
        _collect(db.collection('volunteers').where('user_id', '==', user_id))
    if email:
        _collect(db.collection('volunteers').where('email', '==', email))

    # Group by event, accumulating role labels, applying the attendance filter
    by_event: Dict[str, List[str]] = {}
    for vol in volunteer_docs:
        if not _did_volunteer_attend(vol):
            continue
        event_id = vol.get('event_id')
        if not event_id:
            continue
        vtype = (vol.get('volunteer_type') or '').lower()
        label = ATTENDANCE_ROLE_LABELS.get(vtype)
        if not label:
            continue
        roles = by_event.setdefault(event_id, [])
        if label not in roles:
            roles.append(label)

    # Enrich and shape the response
    results: List[Dict[str, Any]] = []
    for event_id, roles in by_event.items():
        hackathon = _enrich_with_hackathon(db, event_id)
        if not hackathon:
            continue
        results.append({
            "event_id": event_id,
            "title": hackathon.get('title') or hackathon.get('name') or '',
            "start_date": hackathon.get('start_date', ''),
            "end_date": hackathon.get('end_date', ''),
            "location": hackathon.get('location', ''),
            "image_url": hackathon.get('image_url', ''),
            "links": hackathon.get('links', []),
            "nonprofits": hackathon.get('nonprofits', []),
            "devpost_url": hackathon.get('devpost_url') or '',
            "roles": roles,
        })

    results.sort(key=lambda r: r.get('start_date') or '', reverse=True)
    return results



# --- Mentor dashboard (/mentor) ------------------------------------------------

# Fields of the caller's OWN mentor volunteer doc that the /mentor dashboard
# renders. Lean on purpose: this is one request per page load and the page only
# needs the decision state, shift availability and check-in state — never the
# long-form answers, which stay behind GET /api/mentor/application/<event_id>.
_MENTOR_EVENT_SELF_FIELDS = (
    'status', 'inPerson', 'isInPerson', 'availability',
    'checkInTime', 'timeSlot', 'timestamp', 'created_timestamp', 'updated_timestamp',
)

_MENTOR_EVENT_HACKATHON_FIELDS = (
    'title', 'start_date', 'end_date', 'location', 'timezone', 'image_url',
)


def _caller_identities(propel_user_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    (propel UUID, PropelAuth email, OAuth user_id) — every shape a volunteer
    doc may have been keyed on (see find_volunteer_by_caller_identity). Email
    and OAuth id come from the verified token identity, never a request body.
    """
    email = oauth_user_id = None
    try:
        from services.users_service import get_propel_user_details_by_id
        details = get_propel_user_details_by_id(propel_user_id) or ()
        email = details[0] if len(details) > 0 else None
        oauth_user_id = details[1] if len(details) > 1 else None
    except Exception as e:
        warning(logger, "Could not resolve caller identity via PropelAuth",
                propel_user_id=propel_user_id, exc_info=e)
    return propel_user_id, email, oauth_user_id


def _hackathon_summary(db, event_id: str) -> Optional[Dict[str, Any]]:
    """Display fields for one hackathon by event_id — no teams/nonprofits fan-out."""
    try:
        docs = db.collection('hackathons').where('event_id', '==', event_id).limit(1).stream()
        for doc in docs:
            rec = doc.to_dict() or {}
            summary = {k: rec.get(k) for k in _MENTOR_EVENT_HACKATHON_FIELDS}
            summary['id'] = doc.id
            return summary
    except Exception as e:
        warning(logger, "Failed to query hackathon for event_id", event_id=event_id, exc_info=e)
    return None


def _mentor_doc_rank(vol: Dict[str, Any]) -> Tuple[int, str]:
    """When one person has two mentor docs for an event, the approved one wins, then the newest."""
    stamp = vol.get('updated_timestamp') or vol.get('timestamp') or vol.get('created_timestamp') or ''
    return (1 if vol.get('isSelected') else 0, str(stamp))


def get_my_mentor_events(propel_user_id: str) -> List[Dict[str, Any]]:
    """
    Every event the caller has a MENTOR application for, newest event first,
    for the /mentor dashboard (the mentor counterpart of GET /api/judge/
    assignments). Unlike get_user_hackathon_attendance there is NO attendance
    filter — a pending application must show so the mentor can find and edit
    it — and nothing but the mentor role is returned.

    Shape:
        [{
            "event_id", "id", "title", "start_date", "end_date", "location",
            "timezone", "image_url",
            "application": {
                "isSelected": bool, "isCheckedIn": bool,
                "status"?, "inPerson"?, "isInPerson"?, "availability"?,
                "checkInTime"?, "timeSlot"?, "timestamp"?, ...
            },
        }, ...]
    """
    if not propel_user_id:
        return []

    db = get_db()
    propel_id, email, oauth_user_id = _caller_identities(propel_user_id)

    seen_ids = set()
    volunteer_docs: List[Dict[str, Any]] = []

    def _collect(query):
        try:
            for snap in query.stream():
                if snap.id in seen_ids:
                    continue
                seen_ids.add(snap.id)
                data = snap.to_dict() or {}
                data['_doc_id'] = snap.id
                volunteer_docs.append(data)
        except Exception as e:
            warning(logger, "volunteers query failed", exc_info=e)

    _collect(db.collection('volunteers').where('user_id', '==', propel_id))
    if email:
        _collect(db.collection('volunteers').where('email', '==', email))
    if oauth_user_id and oauth_user_id != propel_id:
        _collect(db.collection('volunteers').where('user_id', '==', oauth_user_id))

    by_event: Dict[str, Dict[str, Any]] = {}
    for vol in volunteer_docs:
        if (vol.get('volunteer_type') or '').lower() != 'mentor':
            continue
        event_id = vol.get('event_id')
        if not event_id:
            continue
        current = by_event.get(event_id)
        if current is None or _mentor_doc_rank(vol) > _mentor_doc_rank(current):
            by_event[event_id] = vol

    results: List[Dict[str, Any]] = []
    for event_id, vol in by_event.items():
        hackathon = _hackathon_summary(db, event_id)
        if not hackathon:
            continue
        application = {k: vol.get(k) for k in _MENTOR_EVENT_SELF_FIELDS if vol.get(k) is not None}
        application['isSelected'] = bool(vol.get('isSelected'))
        application['isCheckedIn'] = bool(vol.get('isCheckedIn'))
        # The volunteer doc id — what the share card URL
        # (/hack/<event>/share/mentor/<id>) is keyed on; it is already public
        # on the event roster for selected mentors.
        if vol.get('_doc_id'):
            application['id'] = vol['_doc_id']
        results.append({'event_id': event_id, **hackathon, 'application': application})

    results.sort(key=lambda r: r.get('start_date') or '', reverse=True)
    return results



# The roles a person can hold on an event roster and therefore share a card
# for (sponsors are organizations, not people on the public roster).
SHARE_ROLE_TYPES = ("mentor", "judge", "hacker", "volunteer")


def _find_volunteer_snapshot(db, propel_user_id: str, event_id: str, volunteer_type: str):
    """
    Same three-way identity resolution as find_volunteer_by_caller_identity
    (propel UUID -> verified email -> legacy OAuth user_id) but returns the
    Firestore SNAPSHOT so the caller gets the doc id. find_volunteer_by_*
    return to_dict() copies without the id, and the share-card URL is keyed
    on the volunteer doc id (the same id the public roster exposes).
    """
    if not propel_user_id or not event_id or not volunteer_type:
        return None
    propel_id, email, oauth_user_id = _caller_identities(propel_user_id)

    def _first(field: str, value):
        if not value:
            return None
        try:
            query = db.collection('volunteers').where(field, '==', value) \
                                              .where('event_id', '==', event_id) \
                                              .where('volunteer_type', '==', volunteer_type) \
                                              .limit(1)
            for snap in query.stream():
                return snap
        except Exception as e:
            warning(logger, "volunteers query failed", field=field, exc_info=e)
        return None

    snap = _first('user_id', propel_id)
    if snap is None:
        snap = _first('email', email)
    if snap is None and oauth_user_id and oauth_user_id != propel_id:
        snap = _first('user_id', oauth_user_id)
    return snap


def get_my_roles_for_event(propel_user_id: str, event_id: str) -> Dict[str, Any]:
    """
    GET /api/volunteer/<event_id>/my-roles — every role the caller applied for
    on ONE event, lean: {"roles": [{"type", "id", "isSelected", "name"}]}.
    Backs the event page's "You're on the roster — share it" band and the
    roster "You" tag. Only fields the caller already knows about themselves;
    `id` is the volunteer doc id the public roster exposes for selected people.
    """
    if not propel_user_id or not event_id:
        return {"roles": []}
    db = get_db()
    roles: List[Dict[str, Any]] = []
    for vtype in SHARE_ROLE_TYPES:
        snap = _find_volunteer_snapshot(db, propel_user_id, event_id, vtype)
        if snap is None:
            continue
        data = snap.to_dict() or {}
        roles.append({
            "type": vtype,
            "id": snap.id,
            "isSelected": bool(data.get("isSelected")),
            "name": data.get("name"),
        })
    return {"roles": roles}



MENTOR_CHECKIN_DEFAULT_TIMEZONE = "America/Phoenix"


def mentor_checkin_window(hackathon: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    When mentors may check themselves in for an event:
    opens 00:00 on `start_date`, closes 23:59:59 on `end_date`, both in the
    event's own timezone (`hackathon.timezone`, default America/Phoenix).

    Returns {"state": "upcoming"|"open"|"closed", "opens_at", "closes_at"}
    (ISO strings with offset, or None when the date is unknown). Missing dates
    leave that side of the window open — same reading as the survey mode
    (api.surveys.surveys_service.compute_event_mode), whose date parser is
    reused so the two can't disagree about when an event starts.
    """
    from api.surveys.surveys_service import _parse_event_date

    hackathon = hackathon or {}
    tz_name = hackathon.get("timezone") or MENTOR_CHECKIN_DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone(MENTOR_CHECKIN_DEFAULT_TIMEZONE)

    def _date_part(value):
        # Tolerate "2026-11-14T09:00:00" as well as the usual "2026-11-14".
        return value[:10] if isinstance(value, str) else None

    opens = _parse_event_date(_date_part(hackathon.get("start_date")), tz, end_of_day=False)
    closes = _parse_event_date(_date_part(hackathon.get("end_date")), tz, end_of_day=True)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)

    if opens is not None and current < opens:
        state = "upcoming"
    elif closes is not None and current > closes:
        state = "closed"
    else:
        state = "open"
    return {
        "state": state,
        "opens_at": opens.isoformat() if opens is not None else None,
        "closes_at": closes.isoformat() if closes is not None else None,
    }


def _find_mentor_snapshot(db, user_id: str, event_id: str):
    """
    The caller's mentor doc for an event, through the SAME three-way identity
    resolution as GET /api/mentor/application (propel UUID -> verified email
    -> legacy OAuth id). The check-in routes used the propel-UUID-only lookup,
    so a mentor matched by email saw their application but got a 404 from
    /status and could not check in. Returns (snapshot, data) or (None, None).
    """
    snap = _find_volunteer_snapshot(db, user_id, event_id, 'mentor')
    if snap is None:
        return None, None
    data = snap.to_dict() or {}
    data['id'] = snap.id
    return snap, data


def get_mentor_checkin_status(user_id: str, event_id: str) -> Dict[str, Any]:
    """
    Current check-in status for the calling mentor, plus whether they are
    approved (`isSelected`) and the event's check-in window, so the page never
    has to guess dates client-side.
    """
    db = get_db()
    _snap, volunteer = _find_mentor_snapshot(db, user_id, event_id)

    if not volunteer:
        return {
            'success': False,
            'error': 'Mentor record not found'
        }

    return {
        'success': True,
        'isCheckedIn': volunteer.get('isCheckedIn', False),
        'checkInTime': volunteer.get('checkInTime', None),
        'timeSlot': volunteer.get('timeSlot', None),
        'isSelected': bool(volunteer.get('isSelected')),
        'window': mentor_checkin_window(_hackathon_summary(db, event_id)),
    }


def _checkin_refusal(code: str, message: str, **extra) -> Dict[str, Any]:
    """A check-in the backend refuses on purpose (no write, no Slack)."""
    return {'success': False, 'error': code, 'message': message, **extra}


def mentor_checkin(user_id: str, event_id: str, time_slot: Optional[str] = None) -> Dict[str, Any]:
    """
    Check in a mentor for an event.

    Refuses, in this order, with no write and no Slack:
      not_approved      — the mentor application isn't approved (isSelected)
      checkin_not_open  — the event hasn't started yet (carries opens_at)
      checkin_closed    — the event's last day is over (carries closes_at)
    """
    db = get_db()
    _snap, volunteer = _find_mentor_snapshot(db, user_id, event_id)

    if not volunteer:
        return {
            'success': False,
            'error': 'Mentor record not found'
        }

    if not volunteer.get('isSelected'):
        return _checkin_refusal(
            'not_approved', "Your mentor application hasn't been approved yet.")

    window = mentor_checkin_window(_hackathon_summary(db, event_id))
    if window['state'] == 'upcoming':
        return _checkin_refusal(
            'checkin_not_open', "Mentor check-in opens when the event starts.",
            opens_at=window['opens_at'])
    if window['state'] == 'closed':
        return _checkin_refusal(
            'checkin_closed', "Mentor check-in has closed for this event.",
            closes_at=window['closes_at'])

    volunteer_ref = db.collection('volunteers').document(volunteer['id'])

    # Check if mentor is already checked in
    if volunteer.get('isCheckedIn', False):
        # Update the time slot if provided
        if time_slot:
            volunteer_ref.update({
                'timeSlot': time_slot,
                'updated_timestamp': _get_current_timestamp()
            })

            # Clear caches
            _clear_volunteer_caches(user_id, volunteer.get('email'), event_id, 'mentor')

            return {
                'success': True,
                'message': 'Updated check-in time slot',
                'checkInTime': volunteer.get('checkInTime'),
                'timeSlot': time_slot,
                'slackNotificationSent': False  # No need to send notification for update
            }

        return {
            'success': True,
            'message': 'Already checked in',
            'checkInTime': volunteer.get('checkInTime'),
            'timeSlot': volunteer.get('timeSlot'),
            'slackNotificationSent': False
        }

    # Perform check-in
    current_time = _get_current_timestamp()

    update_data = {
        'isCheckedIn': True, # This is meant just to track mentor Check-in status TODO: change this to mentorIsCheckedIn
        'checkedIn': True, # This field is meant for in-person check-in, we'll also set it here
        # We support this because we have mentors that can check themselves in online
        'checkInTime': current_time,
        # Also add this to checkInTimeList
        'checkInTimeList': firestore.ArrayUnion([current_time]),
        'updated_timestamp': current_time
    }

    if time_slot:
        update_data['timeSlot'] = time_slot

    volunteer_ref.update(update_data)

    # Clear caches
    _clear_volunteer_caches(user_id, volunteer.get('email'), event_id, 'mentor')

    # Send Slack notification
    slack_notification_sent = False
    try:
        slack_notification_sent = send_mentor_checkin_notification(volunteer, time_slot)
    except Exception as e:
        exception(logger, "Failed to send check-in notification", exc_info=e)

    return {
        'success': True,
        'message': 'Checked in successfully',
        'checkInTime': current_time,
        'timeSlot': time_slot,
        'slackNotificationSent': slack_notification_sent
    }


def mentor_checkout(user_id: str, event_id: str) -> Dict[str, Any]:
    """
    Check out a mentor from an event. Always allowed while checked in — even
    after the event (someone still checked in at the end can leave) and even
    if their approval was later withdrawn.
    """
    db = get_db()
    _snap, volunteer = _find_mentor_snapshot(db, user_id, event_id)

    if not volunteer:
        return {
            'success': False,
            'error': 'Mentor record not found'
        }

    # Check if mentor is checked in
    if not volunteer.get('isCheckedIn', False):
        return {
            'success': False,
            'error': 'Mentor is not checked in'
        }
    # Calculate check-in duration if possible
    check_in_time = volunteer.get('checkInTime')
    check_in_duration = None
    
    if check_in_time:
        try:
            # Parse the ISO timestamp
            check_in_datetime = datetime.fromisoformat(check_in_time)
            current_datetime = datetime.fromisoformat(_get_current_timestamp())
            
            # Calculate duration
            duration = current_datetime - check_in_datetime
            total_seconds = duration.total_seconds()
            
            # Format as hours and minutes
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            check_in_duration = f"{hours}h {minutes}m"
        except Exception as e:
            exception(logger, "Error calculating check-in duration", exc_info=e)
    
    # Perform check-out
    current_time = _get_current_timestamp()
    volunteer_ref = db.collection('volunteers').document(volunteer['id'])
    
    update_data = {
        'isCheckedIn': False,
        'checkOutTime': current_time,
        
        # Also add to checkoutTimeList or add if it doesn't exist
        'checkoutTimeList': firestore.ArrayUnion([current_time]),
        'updated_timestamp': current_time
    }
    
    if check_in_duration:
        update_data['checkInDuration'] = check_in_duration
    
    volunteer_ref.update(update_data)
    
    # Clear caches
    _clear_volunteer_caches(user_id, volunteer.get('email'), event_id, 'mentor')
    
    # Send Slack notification
    slack_notification_sent = False
    try:
        slack_notification_sent = send_mentor_checkout_notification(volunteer, check_in_duration)
    except Exception as e:
        exception(logger, "Failed to send check-out notification", exc_info=e)
    
    return {
        'success': True,
        'message': 'Checked out successfully',
        'checkInDuration': check_in_duration,
        'slackNotificationSent': slack_notification_sent
    }


def send_mentor_checkin_notification(volunteer: Dict[str, Any], time_slot: Optional[str] = None) -> bool:
    """
    Send a Slack notification when a mentor checks in.

    Returns True if notification was sent, False if skipped (no Slack account)
    or on error.
    """
    if _notifications_disabled():
        info(logger, "ENVIRONMENT=test — skipping mentor check-in notification")
        return False

    email = volunteer.get('email', '')
    areas_of_expertise = volunteer.get('expertise', [])
    specialties = volunteer.get('softwareEngineeringSpecifics', [])
    linked_in = volunteer.get('linkedinProfile', None)
    in_person = volunteer.get('inPerson', None)

    # Resolve Slack user id — use stored value first, then fall back to email lookup
    # (catches mentors who applied/logged in with Google but have Slack under the same email)
    slack_user_id = volunteer.get('slack_user_id', '')
    if not slack_user_id and email:
        try:
            slack_info = get_slack_user_by_email(email)
            if slack_info:
                slack_user_id = slack_info.get('id', '')
                # Persist back so future check-ins skip the lookup
                if slack_user_id and volunteer.get('id'):
                    try:
                        db = get_db()
                        db.collection('volunteers').document(volunteer['id']).update(
                            {'slack_user_id': slack_user_id}
                        )
                    except Exception:
                        pass  # best-effort; don't block the notification
        except Exception as e:
            logger.warning(f"Slack email lookup failed for {email}: {e}")

    # Gate: no Slack identity means the mentor can't be mentioned or reached in Slack
    if not slack_user_id:
        logger.info(f"Mentor {email} has no Slack account; skipping #ask-a-mentor announcement")
        return False

    name_mention = f"<@{slack_user_id}>"

    # Resolve OHack profile URL via users collection
    profile_line = ""
    try:
        user_doc = None
        if volunteer.get('user_id'):
            user_doc = get_user_by_user_id(volunteer['user_id'])
        if not user_doc and email:
            user_doc = get_user_by_email(email)
        if user_doc and user_doc.get('id'):
            first = volunteer.get('firstName', '')
            last = volunteer.get('lastName', '')
            display_name = volunteer.get('name') or f"{first} {last}".strip() or 'their profile'
            profile_url = f"https://www.ohack.dev/profile/{user_doc['id']}"
            profile_line = f"*Profile:* <{profile_url}|View {display_name}'s profile>"
    except Exception as e:
        logger.warning(f"Could not resolve OHack profile for {email}: {e}")

    # Format expertise / specialties / time slot
    expertise_text = ""
    if areas_of_expertise:
        expertise_text = f"*Expertise:* {', '.join(areas_of_expertise) if isinstance(areas_of_expertise, list) else areas_of_expertise}"

    specialties_text = ""
    if specialties:
        specialties_text = f"*Specialties:* {', '.join(specialties) if isinstance(specialties, list) else specialties}"

    time_text = f"*Time Slot:* {time_slot}" if time_slot else ""

    lines = [
        ":reversecongaparrot:  *Mentor Available!*",
        "",
        f"{name_mention} has checked in and is available to help teams!",
        "",
    ]
    for part in [expertise_text, specialties_text, time_text,
                 f"*LinkedIn:* {linked_in}" if linked_in else "",
                 f"*In-Person:* {in_person}" if in_person else "",
                 profile_line]:
        if part:
            lines.append(part)

    lines += [
        "",
        f"If you're a hacker and need help, reach out to {name_mention} directly or post in #ask-a-mentor so everyone can benefit.",
    ]

    slack_message = "\n".join(lines)

    try:
        send_slack(
            message=slack_message,
            channel="ask-a-mentor",
            icon_emoji=":raising_hand:",
            username="Mentor Check-in"
        )
        logger.info(f"Sent Slack notification about mentor check-in for {email}")
        return True
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False


def send_mentor_checkout_notification(volunteer: Dict[str, Any], duration: Optional[str] = None) -> bool:
    """
    Send a Slack notification when a mentor checks out.
    
    Args:
        volunteer: The volunteer record
        duration: Optional duration of the check-in period
        
    Returns:
        True if notification was sent successfully, False otherwise
    """
    first_name = volunteer.get('firstName', '')
    last_name = volunteer.get('lastName', '')
    slack_user_id = volunteer.get('slack_user_id', '')
    
    # Format the mention if we have a slack user ID
    name_mention = f"<@{slack_user_id}>" if slack_user_id else f"{first_name} {last_name}"
    
    # Format duration if available
    duration_text = ""
    if duration:
        duration_text = f"They were available for {duration}."
    
    slack_message = f"""
:wave: *Mentor Update*

{name_mention} has checked out and is no longer available. {duration_text}

Thanks for your support!
"""
    
    try:
        send_slack(
            message=slack_message,
            channel="ask-a-mentor",
            icon_emoji=":v:",
            username="Mentor Check-in"
        )
        logger.info(f"Sent Slack notification about mentor check-out for {volunteer.get('email')}")
        return True
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False

def generate_qr_code(content: str) -> bytes:
    """
    Generate a QR code image for the given content.
    
    Args:
        content: The text content to encode in the QR code
        
    Returns:
        QR code image as PNG bytes
    """
    try:
        # Create QR code instance with appropriate settings
        qr = qrcode.QRCode(
            version=1,  # Controls the size of the QR Code
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        
        # Add data to the QR code
        qr.add_data(content)
        qr.make(fit=True)
        
        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert PIL image to bytes
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        info(logger, "Generated QR code", content_length=len(content))
        return img_byte_arr
        
    except Exception as e:
        exception(logger, "Error generating QR code", exc_info=e, content=content)
        raise


def _convert_markdown_to_slack_format(message: str) -> str:
    """
    Convert standard markdown to Slack-compatible format using available libraries.
    
    Args:
        message: Message with standard markdown formatting
        
    Returns:
        Message formatted for Slack
    """
    if not message or not isinstance(message, str):
        return message or ""
    
    try:
        # Try slackify library (most reliable for markdown to Slack conversion)
        try:
            from slackify import slackify
            return slackify(message)
        except ImportError:
            pass
        
        # Try slack-sdk with markdown utilities
        try:
            from slack_sdk.web.slack_response import SlackResponse
            # This library doesn't have markdown conversion, skip
            pass
        except ImportError:
            pass
        
        # Manual conversion as fallback (improved version)
        warning(logger, "No slack markdown library found, using manual conversion. Install 'slackify' for better results.")
        
        import re
        slack_message = message
        
        # Convert markdown links [text](url) to Slack format <url|text>
        # This pattern handles most standard markdown links
        link_pattern = r'\[([^\]]*)\]\(([^)]+)\)'
        
        def link_replacer(match):
            text = match.group(1).strip()
            url = match.group(2).strip()
            
            # Clean up the URL
            url = url.strip('\'"')
            
            if text and url:
                # Escape pipe character in link text for Slack
                text = text.replace('|', '\\|')
                return f'<{url}|{text}>'
            elif url:
                return f'<{url}>'
            else:
                return match.group(0)  # Return original if malformed
        
        slack_message = re.sub(link_pattern, link_replacer, slack_message)
        
        # Convert **bold** to *bold* (Slack uses single asterisks for bold)
        slack_message = re.sub(r'\*\*([^*\n]+?)\*\*', r'*\1*', slack_message)
        
        # Convert *italic* to _italic_ (single asterisks that aren't bold)
        # Use negative lookbehind/lookahead to avoid conflicts with bold
        slack_message = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'_\1_', slack_message)
        
        # Convert strikethrough ~~text~~ to ~text~
        slack_message = re.sub(r'~~([^~\n]+?)~~', r'~\1~', slack_message)
        
        # Convert headers to bold text
        slack_message = re.sub(r'^#{1,6}\s*(.+)$', r'*\1*', slack_message, flags=re.MULTILINE)
        
        # Convert bullet points to Slack format
        slack_message = re.sub(r'^(\s*)[-*+]\s+', r'\1• ', slack_message, flags=re.MULTILINE)
        
        # Convert numbered lists to bullet points (Slack doesn't have great numbered list support)
        slack_message = re.sub(r'^(\s*)\d+\.\s+', r'\1• ', slack_message, flags=re.MULTILINE)
        
        # Convert blockquotes
        slack_message = re.sub(r'^>\s*(.+)$', r'┃ \1', slack_message, flags=re.MULTILINE)
        
        # Clean up excessive whitespace
        slack_message = re.sub(r'\n{3,}', '\n\n', slack_message)
        slack_message = slack_message.strip()
        
        return slack_message
        
    except Exception as e:
        exception(logger, "Error converting markdown to Slack format", exc_info=e)
        return message  # Return original message if conversion fails

def _process_qr_code_in_message(message: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    Process QR code placeholders in message and generate attachments.

    Args:
        message: The original message content

    Returns:
        Tuple of (message_for_slack, message_for_email, qr_code_attachments)
    """
    message_for_slack = _convert_markdown_to_slack_format(message)
    message_for_email = message
    qr_code_attachments = []

    qr_code_pattern = r'\[QRCode:(.*?)\]'
    match = re.search(qr_code_pattern, message)
    if match:
        qr_code_content = match.group(1)
        # Remove [QRCode:] from the message for Slack
        message_for_slack = re.sub(qr_code_pattern, '', message_for_slack).strip()

        # Generate the QR code for email using Resend's inline image approach
        qr_code_image = generate_qr_code(qr_code_content)
        qr_code_base64 = base64.b64encode(qr_code_image).decode('utf-8')

        # Create inline image attachment with content_id
        qr_code_attachments.append({
            "content": qr_code_base64,
            "filename": "qr_code.png",
            "content_type": "image/png",
            "content_id": "qr-code-image"
        })

        # Replace placeholder with cid reference
        qr_code_placeholder = f'[QRCode:{qr_code_content}]'
        message_for_email = message.replace(qr_code_placeholder, f'<img src="cid:qr-code-image" alt="QR Code" style="max-width: 200px; height: auto;" />')

    return message_for_slack, message_for_email, qr_code_attachments


def _send_slack_message_to_user(
    slack_user_id: str,
    message: str,
    volunteer_type: str,
    volunteer_id: Optional[str] = None,
    recipient_type: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Send a Slack message to a user.

    Args:
        slack_user_id: The Slack user ID (raw format like "U12345ABC", not OAuth format)
        message: The message content (already converted to Slack format)
        volunteer_type: Type of volunteer
        volunteer_id: Optional volunteer ID for logging
        recipient_type: Optional recipient type for logging

    Returns:
        Tuple of (success, error_message)
    """
    # Validate that this is a valid Slack user ID format
    # Slack user IDs start with 'U' or 'W' followed by alphanumeric characters
    # They should NOT be OAuth format (oauth2|...) or Google IDs
    if not slack_user_id:
        warning(logger, "Empty slack_user_id provided", volunteer_id=volunteer_id)
        return False, "No Slack user ID provided"

    if slack_user_id.startswith('oauth2|'):
        warning(logger, "OAuth format ID passed to Slack messaging - this is not a raw Slack ID",
                slack_user_id=slack_user_id, volunteer_id=volunteer_id)
        return False, "Invalid Slack user ID format (OAuth format not supported for Slack DMs)"

    # Slack user IDs typically start with U or W
    if not (slack_user_id.startswith('U') or slack_user_id.startswith('W')):
        warning(logger, "Potentially invalid Slack user ID format",
                slack_user_id=slack_user_id, volunteer_id=volunteer_id)
        # Continue anyway - it might be a channel name or valid ID we don't recognize

    try:
        slack_message = f"📧 *Message from Opportunity Hack Team*\n\n{message}\n\n_This message was sent to you as a registered {volunteer_type.title()} for Opportunity Hack._"
        send_slack(message=slack_message, channel=slack_user_id)
        info(logger, "Slack message sent to user",
             volunteer_id=volunteer_id, slack_user_id=slack_user_id, recipient_type=recipient_type)
        return True, None
    except Exception as slack_error:
        error(logger, "Failed to send Slack message to user",
              volunteer_id=volunteer_id, exc_info=slack_error)
        return False, str(slack_error)


def _send_email_to_user(
    email: str,
    name: str,
    subject: str,
    message: str,
    recipient_type: str,
    volunteer_type: str,
    qr_code_attachments: Optional[List[Dict[str, Any]]] = None,
    volunteer_id: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Send an email to a user.

    Args:
        email: The recipient email address
        name: The recipient name
        subject: The email subject
        message: The message content (may contain HTML/markdown)
        recipient_type: Type of recipient
        volunteer_type: Type of volunteer
        qr_code_attachments: Optional QR code attachments
        volunteer_id: Optional volunteer ID for logging

    Returns:
        Tuple of (success, error_message)
    """
    import html
    try:
        # Configure resend
        resend.api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')

        # Convert markdown to HTML
        try:
            formatted_message = markdown.markdown(message, extensions=['nl2br', 'fenced_code'])
        except Exception as markdown_error:
            warning(logger, "Failed to convert markdown, falling back to basic formatting", exc_info=markdown_error)
            escaped_message = html.escape(message)
            formatted_message = escaped_message.replace('\n', '<br>')

        email_subject = f"{subject} - Message from Opportunity Hack Team"

        # Enhanced greeting based on recipient type
        greeting_map = {
            'mentor': 'Dear Mentor',
            'sponsor': 'Dear Sponsor',
            'judge': 'Dear Judge',
            'hacker': 'Dear Participant',
            'volunteer': 'Dear Volunteer'
        }
        greeting = greeting_map.get(recipient_type.lower(), 'Dear Volunteer')

        # Create HTML email content
        html_content = f"""
            <h2>{greeting} {html.escape(name)},</h2>
            <p>You have received a message from the Opportunity Hack team:</p>
            <div style="background-color: #f5f5f5; padding: 15px; border-left: 4px solid #007bff; margin: 15px 0; font-family: Arial, sans-serif;">
                <p style="white-space: pre-wrap; margin: 0;">{formatted_message}</p>
            </div>
            <p>Best regards,<br>The Opportunity Hack Team</p>

            <!-- Donation Call-to-Action (Compact) -->
            <div style="background-color: #e8f5e8; padding: 16px; margin: 20px 0; border-radius: 6px; border-left: 3px solid #27ae60; text-align: center;">
                <h4 style="color: #27ae60; margin: 0 0 8px 0; font-size: 16px;">💚 Support Our Mission</h4>
                <p style="margin: 0 0 12px 0; color: #34495e; font-size: 14px;">Just <strong>$17 feeds a hacker</strong> building solutions for nonprofits!</p>
                <div style="margin: 12px 0;">
                    <a href="https://givebutter.com/a5MSes" style="background-color: #27ae60; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 14px; margin: 0 4px;">💳 Donate Now</a>
                    <a href="http://venmo.com/opportunityhack" style="color: #3D95CE; text-decoration: none; font-size: 13px; margin: 0 4px;">Venmo</a>
                    <a href="http://paypal.me/opportunityhack" style="color: #0070ba; text-decoration: none; font-size: 13px, margin: 0 4px;">PayPal</a>
                </div>
                <p style="font-size: 11px; color: #666; margin: 8px 0 0 0;">Corporate employees: Find us on Benevity • 501(c)(3) tax-deductible</p>
            </div>

            <!-- Social Media Footer (Compact) -->
            <div style="background-color: #f8f9fa; padding: 16px; margin: 20px 0; border-radius: 6px; text-align: center;">
                <h4 style="color: #2c3e50; margin: 0 0 12px 0; font-size: 15px;">🌟 Stay Connected</h4>
                <div style="margin: 8px 0;">
                    <a href="https://www.instagram.com/opportunityhack/" style="text-decoration: none; margin: 0 6px; color: #E4405F; font-size: 13px;">Instagram</a> |
                    <a href="https://www.linkedin.com/company/opportunity-hack/" style="text-decoration: none; margin: 0 6px; color: #0A66C2; font-size: 13px;">LinkedIn</a> |
                    <a href="https://slack.ohack.dev" style="text-decoration: none; margin: 0 6px; color: #4A154B; font-size: 13px;">Slack</a> |
                    <a href="https://github.com/opportunity-hack/" style="text-decoration: none; margin: 0 6px; color: #333; font-size: 13px;">GitHub</a> |
                    <! -- Threads link -->
                    <a href="https://www.threads.net/@opportunityhack" style="text-decoration: none; margin: 0 6px; color: #000; font-size: 13px;">Threads</a> |
                    <! -- Facebook link -->
                    <a href="https://www.facebook.com/opportunityhack" style="text-decoration: none; margin: 0 6px; color: #1877F2; font-size: 13px;">Facebook</a>
                </div>
                <p style="font-size: 11px; color: #666; margin: 8px 0 0 0;">Help us reach more people - share our mission! 🚀</p>
            </div>

            <hr>
            <p style="font-size: 12px; color: #666;">
                Sent to {volunteer_type.title()} for Opportunity Hack.
            </p>
            """

        # Send email
        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "reply_to": "Opportunity Hack Questions <questions@ohack.org>",
            "subject": email_subject,
            "html": html_content,
        }

        # Add QR code attachments if they exist
        if qr_code_attachments:
            params["attachments"] = qr_code_attachments

        # Rate limits are retried with backoff inside send_with_retry; if we are
        # still limited, it returns the error message instead of raising, and
        # logs at warning so Sentry is not paged for a "slow down" signal.
        email_result, rate_limit_error = send_with_retry(
            lambda: resend.Emails.send(params),
            log_context={"volunteer_id": volunteer_id, "email": email},
        )
        if rate_limit_error:
            return False, rate_limit_error, None
        resend_email_id = email_result.get('id') if isinstance(email_result, dict) else getattr(email_result, 'id', None)
        info(logger, "Email sent to user",
             volunteer_id=volunteer_id, email=email, result=email_result, resend_email_id=resend_email_id, recipient_type=recipient_type)
        return True, None, resend_email_id

    except Exception as email_error:
        error(logger, "Failed to send email to user",
              volunteer_id=volunteer_id, exc_info=email_error)
        return False, str(email_error), None


def send_volunteer_message(
    volunteer_id: str,
    message: str,
    subject: str,
    admin_user_id: str,
    admin_user: Any = None,
    recipient_type: str = 'volunteer',
    recipient_id: Optional[str] = None,
    fallback_email: Optional[str] = None,
    fallback_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send a message to a volunteer via Slack and email.

    Args:
        volunteer_id: The ID of the volunteer to message
        message: The message content to send
        subject: The email subject
        admin_user_id: The user ID of the admin sending the message
        admin_user: The admin user object
        recipient_type: Type of recipient (mentor, sponsor, judge, volunteer, hacker, etc.)
        recipient_id: Optional specific recipient ID for enhanced context - might be Slack ID

    Returns:
        Dict containing delivery status and volunteer information
    """
    from db.db import get_db

    try:
        # Get volunteer by ID from the volunteers collection
        db = get_db()
        volunteer_doc = db.collection('volunteers').document(volunteer_id).get()
    
        # Normalize recipient_id to use OAuth format if needed
        if recipient_id and not is_oauth_user_id(recipient_id):
            recipient_id = normalize_slack_user_id(recipient_id)
        elif not recipient_id:
            logger.warning("No recipient_id provided, Slack message may not be sent if slack_user_id is not found in volunteer record.")

        # Search users for  "user_id": "<recipient_id>"
        users_doc = db.collection('users').where('user_id', '==', f"{recipient_id}").get()

        email = None
        slack_user_id = None
        name = "Volunteer"
        volunteer_type = recipient_type
        volunteer = {}

        if volunteer_doc.exists:
            volunteer = volunteer_doc.to_dict()

            # Extract contact information from volunteer data
            email = volunteer.get('email')
            # slack_user_id in volunteers collection is only set for actual Slack users
            slack_user_id = volunteer.get('slack_user_id')
            name = volunteer.get('name', 'Volunteer')
            volunteer_type = volunteer.get('volunteer_type', recipient_type)
        elif users_doc:
            user = users_doc[0].to_dict()
            email = user.get('email_address')
            # Only use user_id for Slack messaging if it's actually a Slack user ID
            user_id = user.get('user_id')
            if user_id and is_slack_user_id(user_id):
                # Extract the raw Slack user ID (e.g., "U12345ABC" from "oauth2|slack|T1Q7936BH-U12345ABC")
                slack_user_id = extract_slack_user_id(user_id)
            else:
                # Non-Slack users (Google, GitHub, etc.) can't receive Slack DMs
                slack_user_id = None
            name = user.get('name', 'Volunteer')
            volunteer_type = "Volunteer"
        else:
            # No volunteer or user document found — use fallback email/name from request
            if fallback_email:
                email = fallback_email
                name = fallback_name or 'Volunteer'
            else:
                return {
                    'success': False,
                    'error': 'Volunteer record not found',
                    'volunteer_id': volunteer_id,
                    'recipient_type': recipient_type
                }
        

        if not email:
            return {
                'success': False,
                'error': 'Volunteer email not found',
                'volunteer_id': volunteer_id
            }

        # Process QR code in message
        message_for_slack, message_for_email, qr_code_attachments = _process_qr_code_in_message(message)

        # Track message delivery status
        delivery_status = {
            'slack_sent': False,
            'email_sent': False,
            'slack_error': None,
            'email_error': None
        }

        # Send Slack message if slack_user_id is available
        if slack_user_id:
            slack_success, slack_error = _send_slack_message_to_user(
                slack_user_id=slack_user_id,
                message=message_for_slack,
                volunteer_type=volunteer_type,
                volunteer_id=volunteer_id,
                recipient_type=recipient_type
            )
            delivery_status['slack_sent'] = slack_success
            delivery_status['slack_error'] = slack_error

        # Send email message
        email_success, email_error, resend_email_id = _send_email_to_user(
            email=email,
            name=name,
            subject=subject,
            message=message_for_email,
            recipient_type=recipient_type,
            volunteer_type=volunteer_type,
            qr_code_attachments=qr_code_attachments,
            volunteer_id=volunteer_id
        )
        delivery_status['email_sent'] = email_success
        delivery_status['email_error'] = email_error

        # Update volunteer document with message tracking (sent_emails with Resend ID)
        try:
            admin_full_name = f"Admin ({admin_user.user_id})" if admin_user else "Admin"
            email_subject = f"{subject} - Message from Opportunity Hack Team"

            sent_email_record = {
                'resend_id': resend_email_id,
                'subject': email_subject,
                'timestamp': _get_current_timestamp(),
                'sent_by': admin_full_name,
                'recipient_type': recipient_type,
            }

            # Add the record to the volunteer's sent_emails array
            volunteer_ref = db.collection('volunteers').document(volunteer_id)
            volunteer_ref.set({
                'sent_emails': firestore.ArrayUnion([sent_email_record]),
                'last_message_timestamp': _get_current_timestamp(),
                'updated_timestamp': _get_current_timestamp()
            }, merge=True)
            user_id = volunteer.get('user_id', '')

            info(logger, "Updated volunteer document with sent_emails tracking",
                 volunteer_id=volunteer_id, resend_email_id=resend_email_id,
                 message_timestamp=sent_email_record['timestamp'])

            _clear_volunteer_caches(
                user_id=user_id,
                email=email,
                event_id=volunteer.get('event_id', ''),
                volunteer_type=volunteer_type
            )

        except Exception as tracking_error:
            error(logger, "Failed to update volunteer document with sent_emails tracking",
                  volunteer_id=volunteer_id, resend_email_id=resend_email_id,
                  exc_info=tracking_error)

        # Enhanced Slack audit message with recipient context
        from common.utils.slack import send_slack_audit
        message_preview = message[:100] + "..." if len(message) > 100 else message
        send_slack_audit(
            action="admin_send_volunteer_message",
            message=f"Admin {admin_user_id} sent message to {recipient_type} {volunteer_id} ({name})",
            payload={
                "volunteer_id": volunteer_id,
                "recipient_type": recipient_type,
                "recipient_id": recipient_id,
                "volunteer_email": email,
                "volunteer_slack_id": slack_user_id,
                "volunteer_type": volunteer_type,
                "message_preview": message_preview,
                "delivery_status": delivery_status
            }
        )

        # Determine success based on whether at least one message was sent
        success = delivery_status['slack_sent'] or delivery_status['email_sent']

        # Invalidate Resend email list cache so next fetch picks up the new email
        if delivery_status['email_sent']:
            delete_cached(_RESEND_INDEX_KEY)
            delete_cached(_RESEND_FRESH_KEY)

        result = {
            'success': success,
            'volunteer_id': volunteer_id,
            'recipient_type': recipient_type,
            'recipient_id': recipient_id,
            'volunteer_name': name,
            'volunteer_email': email,
            'volunteer_slack_id': slack_user_id,
            'volunteer_type': volunteer_type,
            'delivery_status': delivery_status
        }

        if not success:
            result['error'] = (
                f"Failed to send message via both Slack and email. "
                f"Slack error: {delivery_status['slack_error']}. "
                f"Email error: {delivery_status['email_error']}"
            )

        return result

    except Exception as e:
        error(logger, "Error sending message to volunteer",
              volunteer_id=volunteer_id, exc_info=e)
        return {
            'success': False,
            'error': f"Failed to send message: {str(e)}",
            'volunteer_id': volunteer_id,
            'recipient_type': recipient_type
        }


def send_email_to_address(
    email: str,
    message: str,
    subject: str,
    admin_user_id: str,
    admin_user: Any = None,
    recipient_type: str = 'volunteer',
    name: Optional[str] = None,
    volunteer_id: Optional[str] = None,
    collection_name: Optional[str] = None,
    document_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send an email to a specific email address using the same template as send_volunteer_message.

    Args:
        email: The recipient email address
        message: The message content to send
        subject: The email subject
        admin_user_id: The user ID of the admin sending the message
        admin_user: The admin user object
        recipient_type: Type of recipient (mentor, sponsor, judge, volunteer, hacker, etc.)
        name: Optional recipient name (defaults to 'Volunteer')
        volunteer_id: Optional volunteer document ID for tracking sent emails

    Returns:
        Dict containing delivery status
    """
    try:
        if not email or '@' not in email:
            return {
                'success': False,
                'error': 'Valid email address is required'
            }

        # Use provided name or default
        recipient_name = name or 'Volunteer'

        # Process QR code in message
        _, message_for_email, qr_code_attachments = _process_qr_code_in_message(message)

        # Send email message
        email_success, email_error, resend_email_id = _send_email_to_user(
            email=email,
            name=recipient_name,
            subject=subject,
            message=message_for_email,
            recipient_type=recipient_type,
            volunteer_type=recipient_type,
            qr_code_attachments=qr_code_attachments,
            volunteer_id=volunteer_id
        )

        admin_full_name = f"Admin ({admin_user.user_id})" if admin_user else "Admin"

        # Save sent_emails record to volunteer document if volunteer_id is provided
        if volunteer_id and email_success:
            try:
                from db.db import get_db
                db = get_db()
                email_subject = f"{subject} - Message from Opportunity Hack Team"

                sent_email_record = {
                    'resend_id': resend_email_id,
                    'subject': email_subject,
                    'timestamp': _get_current_timestamp(),
                    'sent_by': admin_full_name,
                    'recipient_type': recipient_type,
                }

                volunteer_ref = db.collection('volunteers').document(volunteer_id)
                volunteer_ref.set({
                    'sent_emails': firestore.ArrayUnion([sent_email_record]),
                    'last_message_timestamp': _get_current_timestamp(),
                    'updated_timestamp': _get_current_timestamp()
                }, merge=True)

                info(logger, "Updated volunteer document with sent_emails tracking (email-only path)",
                     volunteer_id=volunteer_id, resend_email_id=resend_email_id)

            except Exception as tracking_error:
                error(logger, "Failed to update volunteer document with sent_emails tracking (email-only path)",
                      volunteer_id=volunteer_id, resend_email_id=resend_email_id,
                      exc_info=tracking_error)

        # Track email on any Firestore collection/document (generic tracking)
        if collection_name and document_id and email_success:
            try:
                from db.db import get_db
                db = get_db()
                email_subject = f"{subject} - Message from Opportunity Hack Team"

                sent_email_record = {
                    'resend_id': resend_email_id,
                    'subject': email_subject,
                    'timestamp': _get_current_timestamp(),
                    'sent_by': admin_full_name,
                    'recipient_type': recipient_type,
                }

                doc_ref = db.collection(collection_name).document(document_id)
                doc_ref.update({
                    'sent_emails': firestore.ArrayUnion([sent_email_record]),
                    'last_email_timestamp': _get_current_timestamp(),
                })

                info(logger, "Updated document with sent_emails tracking",
                     collection=collection_name, document_id=document_id,
                     resend_email_id=resend_email_id)

            except Exception as tracking_error:
                error(logger, "Failed to update document with sent_emails tracking",
                      collection=collection_name, document_id=document_id,
                      resend_email_id=resend_email_id, exc_info=tracking_error)

        # Enhanced Slack audit message
        from common.utils.slack import send_slack_audit
        message_preview = message[:100] + "..." if len(message) > 100 else message

        send_slack_audit(
            action="admin_send_email_to_address",
            message=f"Admin {admin_full_name} sent email to {email}",
            payload={
                "recipient_email": email,
                "recipient_name": recipient_name,
                "recipient_type": recipient_type,
                "message_preview": message_preview,
                "email_sent": email_success,
                "email_error": email_error,
                "resend_email_id": resend_email_id,
                "volunteer_id": volunteer_id
            }
        )

        # Invalidate Resend email list cache so next fetch picks up the new email
        if email_success:
            delete_cached(_RESEND_INDEX_KEY)
            delete_cached(_RESEND_FRESH_KEY)

        result = {
            'success': email_success,
            'recipient_email': email,
            'recipient_name': recipient_name,
            'recipient_type': recipient_type,
            'email_sent': email_success,
            'email_error': email_error,
            'resend_email_id': resend_email_id
        }

        if not email_success:
            result['error'] = f"Failed to send email: {email_error}"

        return result

    except Exception as e:
        error(logger, "Error sending email to address", email=email, exc_info=e)
        return {
            'success': False,
            'error': f"Failed to send email: {str(e)}",
            'recipient_email': email
        }


def get_resend_email_statuses(email_ids: list) -> Dict[str, Any]:
    """
    Fetch delivery status for a list of Resend email IDs.
    Results are cached per-id: terminal events for 7 days, transient for 2 minutes.
    """
    try:
        resend.api_key = os.environ.get('RESEND_EMAIL_STATUS_KEY')
        if not resend.api_key:
            return {'success': False, 'error': 'Resend API key not configured'}

        started_at = time.monotonic()

        cached_statuses = {}
        ids_to_fetch = []

        for eid in email_ids[:100]:
            cached = get_cached(f"resend:status:{eid}")
            if cached is not None:
                cached_statuses[eid] = cached
            else:
                ids_to_fetch.append(eid)

        def _fetch_one_status(eid):
            """Fetch one uncached status, with the same retry behavior the
            old sequential loop had: 2 attempts, 0.6s apart, then an
            'unknown' result (failures are not cached)."""
            retries = 0
            while retries < 2:
                try:
                    email_data = resend.Emails.get(eid)
                    last_event = getattr(email_data, 'last_event', '') if not isinstance(email_data, dict) else email_data.get('last_event', '')
                    result = {
                        'id': eid,
                        'to': getattr(email_data, 'to', []) if not isinstance(email_data, dict) else email_data.get('to', []),
                        'subject': getattr(email_data, 'subject', '') if not isinstance(email_data, dict) else email_data.get('subject', ''),
                        'created_at': getattr(email_data, 'created_at', '') if not isinstance(email_data, dict) else email_data.get('created_at', ''),
                        'last_event': last_event,
                    }
                    ttl = _RESEND_STATUS_TERMINAL_TTL if last_event in _RESEND_STATUS_TERMINAL_EVENTS else _RESEND_STATUS_TRANSIENT_TTL
                    set_cached(f"resend:status:{eid}", result, ttl=ttl)
                    return result
                except Exception as fetch_error:
                    retries += 1
                    if retries < 2:
                        time.sleep(0.6)
                        continue
                    warning(logger, "Failed to fetch Resend email status after retry",
                            resend_email_id=eid, exc_info=fetch_error)
                    return {'id': eid, 'last_event': 'unknown', 'error': str(fetch_error)}

        fetched_statuses = {}
        if ids_to_fetch:
            # Fetch uncached IDs in parallel. The old loop went one at a
            # time with a 0.3s sleep before each call, so a cold-cache
            # batch of 100 took ~90 seconds (Sentry performance report).
            # 5 workers: Resend's limit is 10 requests/second, so 5
            # concurrent calls stay well under it even when retries fire,
            # and a cold batch now finishes in a few seconds.
            with ThreadPoolExecutor(max_workers=5) as executor:
                for eid, result in zip(ids_to_fetch, executor.map(_fetch_one_status, ids_to_fetch)):
                    fetched_statuses[eid] = result

        # Assemble exactly like the sequential version did: cached entries
        # first (in input order), then freshly fetched ones (in input
        # order). Both dicts above were built in input order.
        statuses = {**cached_statuses, **fetched_statuses}

        info(logger, "Fetched Resend email statuses",
             requested=len(email_ids[:100]), from_cache=len(cached_statuses),
             fetched=len(fetched_statuses),
             elapsed_seconds=round(time.monotonic() - started_at, 2))

        return {'success': True, 'statuses': statuses}

    except Exception as e:
        error(logger, "Error fetching Resend email statuses", exc_info=e)
        return {'success': False, 'error': str(e)}


# Cache keys for stale-while-revalidate pattern
_RESEND_INDEX_KEY = "resend:all_emails_index"       # full data, long TTL (stale)
_RESEND_FRESH_KEY = "resend:all_emails_fresh"       # freshness flag, short TTL
_RESEND_LOCK_KEY  = "resend:all_emails_refreshing"  # background-refresh lock

_RESEND_STALE_TTL = 3600   # keep data for 1 hour
_RESEND_FRESH_TTL = 900    # re-fetch after 15 minutes (on-demand sync, not per-page-load)
_RESEND_LOCK_TTL  = 120    # lock expires after 2 minutes (prevents stampede)

# Per-ID status cache TTLs
_RESEND_STATUS_TERMINAL_TTL = 7 * 24 * 3600  # terminal events don't change — 7 days
_RESEND_STATUS_TRANSIENT_TTL = 120           # transient events may update — 2 minutes
_RESEND_STATUS_TERMINAL_EVENTS = frozenset({
    'delivered', 'bounced', 'complained', 'clicked', 'opened', 'unsubscribed', 'failed', 'canceled',
})


def _fetch_and_cache_resend_emails(since_days: int = 90) -> Optional[Dict[str, Any]]:
    """
    Fetch recent pages from Resend and update both cache keys.
    Returns the full payload dict on success, None on failure.
    Called only from a background thread — never inline.
    """
    resend.api_key = os.environ.get('RESEND_EMAIL_STATUS_KEY')
    if not resend.api_key:
        return None

    cutoff_dt = datetime.now(pytz.utc) - timedelta(days=since_days)
    all_emails = []
    max_pages = 30
    params = {"limit": 100}
    page_error_occurred = False
    truncated = False
    date_cutoff_reached = False

    for page in range(max_pages):
        if page > 0:
            time.sleep(0.5)
        while True:
            try:
                # Same retry policy as the send path (services/resend_utils.py):
                # only rate limits are retried, with backoff. A rate limit that
                # outlasts the retries comes back as rate_limit_error instead of
                # raising, so it can be logged at warning level -- a "slow down"
                # signal must not page Sentry (issue 7778044789).
                response, rate_limit_error = send_with_retry(
                    lambda: resend.Emails.list(params),
                    log_context={"page": page, "operation": "list_resend_emails"},
                )
                if rate_limit_error:
                    warning(logger, "Resend emails page fetch stopped by rate limit",
                            page=page, attempts=RESEND_MAX_ATTEMPTS,
                            error=rate_limit_error)
                    page_error_occurred = True
                    break
                email_list = response.get('data', []) if isinstance(response, dict) else getattr(response, 'data', [])
                all_emails.extend(email_list)
                info(logger, "Fetched Resend emails page",
                     page=page, count=len(email_list), total_so_far=len(all_emails))

                has_more = response.get('has_more', False) if isinstance(response, dict) else getattr(response, 'has_more', False)
                if not has_more or len(email_list) == 0:
                    break

                # Stop when the oldest item on this page predates cutoff (results are newest-first)
                last_item = email_list[-1]
                last_created = last_item.get('created_at', '') if isinstance(last_item, dict) else getattr(last_item, 'created_at', '')
                if last_created:
                    try:
                        last_dt_str = last_created.rstrip('Z').split('.')[0]
                        last_dt = datetime.fromisoformat(last_dt_str).replace(tzinfo=pytz.utc)
                        if last_dt < cutoff_dt:
                            date_cutoff_reached = True
                    except (ValueError, AttributeError):
                        pass

                if date_cutoff_reached:
                    break

                last_id = last_item.get('id', '') if isinstance(last_item, dict) else getattr(last_item, 'id', '')
                if not last_id:
                    break
                params = {"limit": 100, "after": last_id}
                break  # success — move to next page

            except Exception as page_error:
                # send_with_retry retries only rate limits, so anything that
                # reaches this handler failed on the first attempt and is a
                # real error, not a "slow down" signal.
                error(logger, "Error fetching Resend emails page",
                      page=page, exc_info=page_error)
                page_error_occurred = True
                break
        if page_error_occurred or date_cutoff_reached:
            break
    else:
        truncated = True
        warning(logger, "Resend email pagination hit safety cap",
                max_pages=max_pages, total_so_far=len(all_emails))

    if page_error_occurred:
        if not all_emails:
            # No data at all — release lock so the next request can retry
            delete_cached(_RESEND_LOCK_KEY)
            return None
        # We have partial data from earlier pages — use it rather than discarding
        warning(logger, "Returning partial Resend email data due to fetch error",
                total_fetched=len(all_emails))
        truncated = True

    # Build index by recipient email
    index: Dict[str, List[Dict]] = {}
    for email_data in all_emails:
        if isinstance(email_data, dict):
            recipients = email_data.get('to', [])
            email_id = email_data.get('id', '')
            subject = email_data.get('subject', '')
            created_at = email_data.get('created_at', '')
            last_event = email_data.get('last_event', '')
        else:
            recipients = getattr(email_data, 'to', [])
            email_id = getattr(email_data, 'id', '')
            subject = getattr(email_data, 'subject', '')
            created_at = getattr(email_data, 'created_at', '')
            last_event = getattr(email_data, 'last_event', '')

        if isinstance(recipients, str):
            recipients = [recipients]

        entry = {'id': email_id, 'subject': subject,
                 'created_at': created_at, 'last_event': last_event}

        for recipient in recipients:
            key = recipient.lower().strip()
            if key not in index:
                index[key] = []
            index[key].append(entry)

    total_fetched = len(all_emails)
    info(logger, "Built Resend email index",
         total_fetched=total_fetched, unique_recipients=len(index),
         truncated=truncated)

    payload = {
        'emails_by_recipient': index,
        'total_fetched': total_fetched,
        'truncated': truncated
    }
    # Store data with long TTL; store freshness flag with short TTL
    set_cached(_RESEND_INDEX_KEY, payload, ttl=_RESEND_STALE_TTL)
    set_cached(_RESEND_FRESH_KEY, True, ttl=_RESEND_FRESH_TTL)
    delete_cached(_RESEND_LOCK_KEY)
    return payload


def _background_refresh_resend_emails() -> None:
    """Fire-and-forget background refresh wrapped in try/except."""
    try:
        _fetch_and_cache_resend_emails()
        info(logger, "Background Resend email cache refresh completed")
    except Exception as bg_err:
        error(logger, "Background Resend email cache refresh failed", exc_info=bg_err)
        delete_cached(_RESEND_LOCK_KEY)


def list_all_resend_emails(filter_emails=None, force: bool = False):
    """
    Return the cached Resend email index. Never blocks on a full list crawl.

    Strategies:
    - force=True  → kick off a background refresh (ignoring freshness), return current data + syncing=True
    - Stale cache  → kick off background refresh, return stale data immediately
    - No cache     → kick off background refresh, return empty data + syncing=True
    - Fresh cache  → return immediately

    Args:
        filter_emails: Optional list of email addresses to filter results for.
        force: If True, always start a background refresh.

    Returns:
        Dict with 'success', 'emails_by_recipient', 'total_fetched', 'syncing'.
    """
    try:
        resend.api_key = os.environ.get('RESEND_EMAIL_STATUS_KEY')
        if not resend.api_key:
            return {'success': False, 'error': 'Resend API key not configured'}

        is_fresh = get_cached(_RESEND_FRESH_KEY) is not None
        cached = get_cached(_RESEND_INDEX_KEY)

        def _maybe_start_background():
            if get_cached(_RESEND_LOCK_KEY) is None:
                set_cached(_RESEND_LOCK_KEY, True, ttl=_RESEND_LOCK_TTL)
                threading.Thread(target=_background_refresh_resend_emails, daemon=True).start()
                return True
            return False

        syncing = False

        if force:
            started = _maybe_start_background()
            # Report syncing=True even if lock was already held (another admin's sync is running)
            syncing = started or (get_cached(_RESEND_LOCK_KEY) is not None)
            info(logger, "Force-sync requested for Resend email cache",
                 started_new=started, syncing=syncing)
        elif cached is None:
            syncing = _maybe_start_background()
            info(logger, "No Resend email cache — background sync started", syncing=syncing)
            return {
                'success': True,
                'emails_by_recipient': {},
                'total_fetched': 0,
                'truncated': False,
                'from_cache': False,
                'syncing': True,
            }
        elif not is_fresh:
            syncing = _maybe_start_background()
            info(logger, "Stale Resend email cache — background refresh triggered",
                 total_fetched=cached.get('total_fetched', 0), syncing=syncing)
        else:
            info(logger, "Using fresh cached Resend email index",
                 total_emails=cached.get('total_fetched', 0))

        if cached is None:
            return {
                'success': True,
                'emails_by_recipient': {},
                'total_fetched': 0,
                'truncated': False,
                'from_cache': False,
                'syncing': syncing,
            }

        index = cached['emails_by_recipient']
        if filter_emails:
            filter_set = {e.lower() for e in filter_emails}
            index = {k: v for k, v in index.items() if k in filter_set}
        return {
            'success': True,
            'emails_by_recipient': index,
            'total_fetched': cached['total_fetched'],
            'truncated': cached.get('truncated', False),
            'from_cache': True,
            'stale': not is_fresh,
            'syncing': syncing,
        }

    except Exception as e:
        error(logger, "Error listing Resend emails", exc_info=e)
        return {'success': False, 'error': str(e)}
