from typing import Dict, List, Optional, Union, Any, Tuple
import uuid
import time
from datetime import datetime
import pytz
from functools import lru_cache
from ratelimiter import RateLimiter
from db.db import get_db
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from common.utils.slack import get_slack_user_by_email, send_slack
from common.log import get_logger, info, debug, warning, error, exception
from common.utils.redis_cache import redis_cached, delete_cached, clear_pattern, get_cached, set_cached
from common.utils.oauth_providers import SLACK_PREFIX, normalize_slack_user_id, is_oauth_user_id, is_slack_user_id, extract_slack_user_id
import os
import requests
import resend
import stripe
import markdown
import re
import base64
import qrcode
import io

logger = get_logger("services.volunteers_service")

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
    calendar_attachments: Optional[List[Dict[str, Any]]] = None, event_id: Optional[str] = None                                   
                                      ) -> bool:
    """
    Send a confirmation email to the volunteer who submitted the form.
    
    Args:
        first_name: First name of the volunteer
        last_name: Last name of the volunteer
        email: Email address of the volunteer
        volunteer_type: Type of volunteer (mentor, sponsor, judge)
        calendar_attachments: Optional list of calendar attachments
        event_id: Event ID for generating links
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        error(logger, "Missing required environment variable", var_name="RESEND_WELCOME_EMAIL_KEY")
        return False
    
    resend.api_key = resend_api_key
    
    try:
        volunteer_type_readable = volunteer_type.capitalize()
        is_reviewed_role = volunteer_type.lower() in ("mentor", "judge")
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
        intro_line = (
            f"Thanks for applying as a <strong>{volunteer_type_readable}</strong> for Opportunity Hack — your application has been received."
            if is_reviewed_role
            else f"Thank you for applying as a <strong>{volunteer_type_readable}</strong> for Opportunity Hack. We've received your information and our team will review it shortly."
        )

        params = {
            "from": "Opportunity Hack <welcome@notifs.ohack.org>",
            "to": [email],
            "subject": f"{subject_prefix}Thanks for your Opportunity Hack {volunteer_type_readable} application",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border-radius: 5px;">
                <div style="text-align: center; margin-bottom: 20px;">
                    <img src="https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Dark_Blue.png" alt="Opportunity Hack Logo" style="max-width: 150px;">
                </div>
                <h2 style="color: #3498db; text-align: center;">Thanks for your Opportunity Hack {volunteer_type_readable} application!</h2>
                <p style="font-size: 16px;">Hello {full_name},</p>
                <p style="font-size: 16px;">{intro_line}</p>
                {pending_review_banner}
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
        
        resend.Emails.send(params)
        info(logger, "Sent confirmation email to volunteer", email=email, attachment_count=len(calendar_attachments) if calendar_attachments else 0)
        return True
    except Exception as e:
        exception(logger, "Error sending email via Resend", exc_info=e, email=email)
        return False

def send_admin_notification_email(volunteer_data: Dict[str, Any], is_update: bool = False) -> bool:
    """
    Send a notification email to the admin when a volunteer form is submitted or updated.
    
    Args:
        volunteer_data: The volunteer data
        is_update: Whether this is an update to an existing volunteer
        
    Returns:
        True if email was sent successfully, False otherwise
    """
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
        detail_lines.append(f"*Available Days:* {available_days}")

    skills = volunteer_data.get('skills', '')
    if skills:
        detail_lines.append(f"*Skills:* {skills}")

    experience = volunteer_data.get('experience', '')
    if experience:
        detail_lines.append(f"*Experience:* {experience}")

    if detail_lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(detail_lines)}})

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

def get_calendar_email_attachment_from_availability(
    availability: str, 
    volunteer_email: str,
    event_name: str = "Opportunity Hack Volunteering", 
    location: str = "Virtual",
    organizer_email: str = "welcome@ohack.dev",
    year: Optional[int] = None,
    volunteer_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Parse availability string and generate calendar attachment data for email.
    
    Args:
        availability: String containing availability information
        volunteer_email: Email of the volunteer (for proper ATTENDEE field)
        event_name: Name of the calendar event
        location: Location of the event
        organizer_email: Email of the event organizer
        year: Year for the event (defaults to current year if not specified)
        
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
    
    # Parse timezone
    timezone_match = re.search(r'\(.*?(\w+)\)', availability)
    timezone = timezone_match.group(1) if timezone_match else "PST"
    
    # Check if there's a more explicit timezone indicator
    if "PST" in availability:
        timezone = "PST"
    elif "EST" in availability:
        timezone = "EST"
    elif "MST" in availability:
        timezone = "MST"
    elif "CST" in availability:
        timezone = "CST"
    
    # Split into individual time slots - need to be smarter about comma splitting
    # The issue is that "Sunday, Oct 12: ☀️ Morning" gets split on the comma between Sunday and Oct
    # We need to split on commas that are followed by a day name pattern
    
    # Use regex to split on commas that are followed by weekday patterns
    import re
    time_slot_pattern = r',\s*(?=[A-Za-z]+day,)'  # Split on comma followed by weekday,
    time_slots = re.split(time_slot_pattern, availability)
    time_slots = [slot.strip() for slot in time_slots if slot.strip()]
    
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
            warning(logger, "FAILED: Pattern 1 did not match", pattern=pattern1, slot=slot.strip())
            
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
                warning(logger, "FAILED: Pattern 2 did not match", pattern=pattern2, slot=slot.strip())
                
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
                    error(logger, "CRITICAL: All patterns failed to match slot", 
                          slot=slot.strip(),
                          pattern1=pattern1,
                          pattern2=pattern2, 
                          pattern3=pattern3,
                          slot_length=len(slot.strip()),
                          slot_chars=[ord(c) for c in slot.strip()[:50]]  # Show character codes for debugging
                          )
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
        
        # Find the time slot in our mapping
        slot_times = None
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
            
            # Format dates for iCalendar (UTC format for better compatibility)
            start_str = start_date.strftime("%Y%m%dT%H%M%S")
            end_str = end_date.strftime("%Y%m%dT%H%M%S")
            created_str = datetime.now().strftime("%Y%m%dT%H%M%S")
            debug(logger, "Calendar event time range", start=start_str, end=end_str)
            
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
DTSTART:{start_str}{timezone}
DTEND:{end_str}{timezone}
DTSTAMP:{created_str}{timezone}
CREATED:{created_str}{timezone}
LAST-MODIFIED:{created_str}{timezone}
SUMMARY:{event_name} - {time_emoji_name}
DESCRIPTION:{volunteer_type} availability time slot for {weekday}, {month_name} {day} - {time_emoji_name}. Thank you for volunteering with Opportunity Hack!
LOCATION:{location}
ORGANIZER:MAILTO:{organizer_email}
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

    # Validate required question answers for hacker applications
    if volunteer_data.get('volunteer_type') == 'hacker':
        submitted_answers = volunteer_data.get('requiredQuestionAnswers', [])
        if submitted_answers:
            try:
                db_temp = get_db()
                hackathon_docs = db_temp.collection('hackathons').where('event_id', '==', event_id).limit(1).stream()
                hackathon_data = None
                for doc in hackathon_docs:
                    hackathon_data = doc.to_dict()
                    break

                if hackathon_data:
                    questions = hackathon_data.get('constraints', {}).get('hacker_required_questions', {}).get('questions', [])
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
    
    # Check if volunteer already exists
    existing = get_volunteer_by_user_id(user_id, event_id, volunteer_type)
    
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
        
        # Keep existing isSelected status if not explicitly provided
        if 'isSelected' not in volunteer_data:
            update_data['isSelected'] = existing.get('isSelected', False)

        # Log volunteer_data
        logger.debug(f"volunteer_data: {volunteer_data}")
        logger.debug(f"Updating volunteer record {volunteer_id} with complete data including new fields {update_data}")
        
        # Use set with merge=True to ensure all fields are updated, including new ones
        volunteer_ref.set(update_data, merge=True)

        calendar_attachments = get_calendar_email_attachment_from_availability(
            volunteer_data.get('availability', ''),
            email,  # Pass volunteer email for proper ATTENDEE field
            event_name="Opportunity Hack Volunteering",
            location="Virtual",
            organizer_email="welcome@ohack.dev",
            volunteer_type=volunteer_type
        )
        
        # Clear all related caches
        _clear_volunteer_caches(user_id, email, event_id, volunteer_type)
        
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
                warning(logger, "No calendar attachments generated despite availability data", email=email)
                
            send_volunteer_confirmation_email(first_name, last_name, email, volunteer_type, calendar_attachments, event_id)

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

        calendar_attachments = get_calendar_email_attachment_from_availability(
            volunteer_data.get('availability', ''),
            email,  # Pass volunteer email for proper ATTENDEE field
            event_name="Opportunity Hack Volunteering",
            location="Virtual",
            organizer_email="welcome@ohack.dev",
            volunteer_type=volunteer_type
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
                warning(logger, "No calendar attachments generated despite availability data", email=email)
                
            send_volunteer_confirmation_email(first_name, last_name, email, volunteer_type, calendar_attachments, event_id)
            send_admin_notification_email(volunteer_doc)
            send_slack_volunteer_notification(volunteer_doc)
            info(logger, "Sent notifications for new volunteer", email=email)
        except Exception as e:
            exception(logger, "Failed to send notifications for new volunteer", exc_info=e, email=email)
        
        return volunteer_doc

def update_volunteer_selection(volunteer_id: str, selected: bool, updated_by: str) -> Dict[str, Any]:
    """
    Update the selection status of a volunteer.
    
    Args:
        volunteer_id: The volunteer ID
        selected: The selection status (True/False)
        updated_by: User who made the update
        
    Returns:
        The updated volunteer record
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
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured on this server")
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

    # Remove sensitive info from the fields like: volunteers = [{k: v for k, v in volunteer.items() if k != "email" and k != "ageRange" and k != "shirtSize" and k != "dietaryRestrictions"} for volunteer in volunteers]
    hacker_dict = [{k: v for k, v in hacker.items() if k != "email" and k != "ageRange" and k != "shirtSize" and k != "dietaryRestrictions"} for hacker in hacker_dict]
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
                volunteer_docs.append(snap.to_dict() or {})
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




def get_mentor_checkin_status(user_id: str, event_id: str) -> Dict[str, Any]:
    """
    Get the current check-in status for a mentor.
    
    Args:
        user_id: The mentor's user ID
        event_id: The event ID
        
    Returns:
        Dictionary containing check-in status information
    """
    db = get_db()
    volunteer = get_volunteer_by_user_id(user_id, event_id, 'mentor')
    
    if not volunteer:
        return {
            'success': False,
            'error': 'Mentor record not found'
        }
    
    return {
        'success': True,
        'isCheckedIn': volunteer.get('isCheckedIn', False),
        'checkInTime': volunteer.get('checkInTime', None),        
        'timeSlot': volunteer.get('timeSlot', None)
    }


def mentor_checkin(user_id: str, event_id: str, time_slot: Optional[str] = None) -> Dict[str, Any]:
    """
    Check in a mentor for an event.
    
    Args:
        user_id: The mentor's user ID
        event_id: The event ID
        time_slot: Optional time slot for the check-in
        
    Returns:
        Dictionary containing check-in status information
    """
    db = get_db()
    # Get the volunteer record
    volunteer = get_volunteer_by_user_id(user_id, event_id, 'mentor')
    
    if not volunteer:
        return {
            'success': False,
            'error': 'Mentor record not found'
        }
    
    # Check if mentor is already checked in
    if volunteer.get('isCheckedIn', False):
        # Update the time slot if provided
        if time_slot:
            volunteer_ref = db.collection('volunteers').document(volunteer['id'])
            volunteer_ref.update({
                'timeSlot': time_slot,
                'updated_timestamp': _get_current_timestamp()
            })
            
            # Clear caches
            _clear_volunteer_caches(user_id, volunteer['email'], event_id, 'mentor')
            
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
    volunteer_ref = db.collection('volunteers').document(volunteer['id'])
    
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
    _clear_volunteer_caches(user_id, volunteer['email'], event_id, 'mentor')
    
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
    Check out a mentor from an event.
    
    Args:
        user_id: The mentor's user ID
        event_id: The event ID
        
    Returns:
        Dictionary containing check-out status information
    """
    db = get_db()
    # Get the volunteer record
    volunteer = get_volunteer_by_user_id(user_id, event_id, 'mentor')
    
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
    _clear_volunteer_caches(user_id, volunteer['email'], event_id, 'mentor')
    
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
    
    Args:
        volunteer: The volunteer record
        time_slot: Optional time slot for the check-in
        
    Returns:
        True if notification was sent successfully, False otherwise
    """
    areas_of_expertise = volunteer.get('expertise', [])
    specialties = volunteer.get('softwareEngineeringSpecifics', [])
    slack_user_id = volunteer.get('slack_user_id', '')
    linked_in = volunteer.get('linkedinProfile', None)
    in_person = volunteer.get('inPerson', None)
    
    # Format the mention if we have a slack user ID
    name_mention = f"<@{slack_user_id}>" if slack_user_id else None
    
    # Format areas of expertise if available
    expertise_text = ""
    if areas_of_expertise:
        if isinstance(areas_of_expertise, list):
            expertise_text = f"*Expertise:* {', '.join(areas_of_expertise)}"
        else:
            expertise_text = f"*Expertise:* {areas_of_expertise}"
    
    # Format specialties if available
    specialties_text = ""
    if specialties:
        if isinstance(specialties, list):
            specialties_text = f"*Specialties:* {', '.join(specialties)}"
        else:
            specialties_text = f"*Specialties:* {specialties}"
    
    # Format time slot if available
    time_text = ""
    if time_slot:
        time_text = f"*Time Slot:* {time_slot}"
    
    slack_message = f"""
:reversecongaparrot:  *Mentor Available!*

{name_mention} has checked in and is available to help teams!

{expertise_text}
{specialties_text}
{time_text}
{f"*LinkedIn:* {linked_in}" if linked_in else ""}
{f"*In-Person:* {in_person}" if in_person else ""}

If you're a hacker and need help, reach out to {name_mention} directly or #ask-a-mentor so everyone can benefit from their expertise and answers.
"""
    
    try:
        send_slack(
            message=slack_message,
            channel="ask-a-mentor",
            icon_emoji=":raising_hand:",
            username="Mentor Check-in"
        )
        logger.info(f"Sent Slack notification about mentor check-in for {volunteer.get('email')}")
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

        email_result = resend.Emails.send(params)
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
            delete_cached("resend:all_emails_index")

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
            delete_cached("resend:all_emails_index")

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

    Args:
        email_ids: List of Resend email IDs to look up

    Returns:
        Dict with 'statuses' mapping each email ID to its Resend status
    """
    try:
        resend.api_key = os.environ.get('RESEND_EMAIL_STATUS_KEY')
        if not resend.api_key:
            return {'success': False, 'error': 'Resend API key not configured'}

        statuses = {}
        for eid in email_ids[:50]:  # Cap at 50 to avoid excessive API calls
            try:
                email_data = resend.Emails.get(eid)
                statuses[eid] = {
                    'id': eid,
                    'to': getattr(email_data, 'to', []) if not isinstance(email_data, dict) else email_data.get('to', []),
                    'subject': getattr(email_data, 'subject', '') if not isinstance(email_data, dict) else email_data.get('subject', ''),
                    'created_at': getattr(email_data, 'created_at', '') if not isinstance(email_data, dict) else email_data.get('created_at', ''),
                    'last_event': getattr(email_data, 'last_event', '') if not isinstance(email_data, dict) else email_data.get('last_event', ''),
                }
            except Exception as fetch_error:
                warning(logger, "Failed to fetch Resend email status",
                        resend_email_id=eid, exc_info=fetch_error)
                statuses[eid] = {
                    'id': eid,
                    'last_event': 'unknown',
                    'error': str(fetch_error)
                }

        return {'success': True, 'statuses': statuses}

    except Exception as e:
        error(logger, "Error fetching Resend email statuses", exc_info=e)
        return {'success': False, 'error': str(e)}


def list_all_resend_emails(filter_emails=None):
    """
    Fetch all sent emails from Resend's List Emails API, cached in Redis.
    Returns an index of {recipient_email: [{id, subject, created_at, last_event}, ...]}.

    Args:
        filter_emails: Optional list of email addresses to filter results for.

    Returns:
        Dict with 'success', 'emails_by_recipient', and 'total_fetched'.
    """
    try:
        resend.api_key = os.environ.get('RESEND_EMAIL_STATUS_KEY')
        if not resend.api_key:
            return {'success': False, 'error': 'Resend API key not configured'}

        cache_key = "resend:all_emails_index"
        cached = get_cached(cache_key)
        if cached is not None:
            info(logger, "Using cached Resend email index",
                 total_emails=cached.get('total_fetched', 0))
            index = cached['emails_by_recipient']
            if filter_emails:
                filter_set = {e.lower() for e in filter_emails}
                index = {k: v for k, v in index.items() if k in filter_set}
            return {
                'success': True,
                'emails_by_recipient': index,
                'total_fetched': cached['total_fetched'],
                'truncated': cached.get('truncated', False),
                'from_cache': True
            }

        # Paginate through resend.Emails.list() (requires resend >= 2.5)
        # Safety cap of 100 pages (~10,000 emails); for-else detects if we hit it.
        all_emails = []
        max_pages = 100
        max_retries = 2
        params = {"limit": 100}
        page_error_occurred = False
        truncated = False

        for page in range(max_pages):
            retries = 0
            while True:
                try:
                    response = resend.Emails.list(params)
                    email_list = response.get('data', []) if isinstance(response, dict) else getattr(response, 'data', [])
                    all_emails.extend(email_list)
                    info(logger, "Fetched Resend emails page",
                         page=page, count=len(email_list), total_so_far=len(all_emails))

                    has_more = response.get('has_more', False) if isinstance(response, dict) else getattr(response, 'has_more', False)
                    if not has_more or len(email_list) == 0:
                        break

                    # Use last email ID as cursor for next page
                    last_item = email_list[-1]
                    last_id = last_item.get('id', '') if isinstance(last_item, dict) else getattr(last_item, 'id', '')
                    if not last_id:
                        break
                    params = {"limit": 100, "after": last_id}
                    break  # success — move to next page

                except Exception as page_error:
                    retries += 1
                    if retries <= max_retries:
                        warning(logger, "Retrying Resend emails page fetch",
                                page=page, retry=retries, exc_info=page_error)
                        time.sleep(1 * retries)
                        continue
                    error(logger, "Error fetching Resend emails page after retries",
                          page=page, exc_info=page_error)
                    page_error_occurred = True
                    break
            if page_error_occurred:
                break
        else:
            # Loop exhausted max_pages without a natural break — results are truncated.
            truncated = True
            warning(logger, "Resend email pagination hit safety cap",
                    max_pages=max_pages, total_so_far=len(all_emails))

        # If a page fetch failed, return an error rather than caching partial data.
        if page_error_occurred:
            return {
                'success': False,
                'error': 'Failed to fetch all email pages from Resend',
                'emails_by_recipient': {},
                'total_fetched': len(all_emails)
            }

        # Build index by recipient email
        index = {}
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

            entry = {
                'id': email_id,
                'subject': subject,
                'created_at': created_at,
                'last_event': last_event,
            }

            for recipient in recipients:
                key = recipient.lower().strip()
                if key not in index:
                    index[key] = []
                index[key].append(entry)

        total_fetched = len(all_emails)

        info(logger, "Built Resend email index",
             total_fetched=total_fetched, unique_recipients=len(index),
             truncated=truncated)

        # Cache the full index with 300s TTL, including empty results so we
        # don't hammer the Resend API on every request when there are no emails.
        set_cached(cache_key, {
            'emails_by_recipient': index,
            'total_fetched': total_fetched,
            'truncated': truncated
        }, ttl=300)

        # Filter if requested
        if filter_emails:
            filter_set = {e.lower() for e in filter_emails}
            index = {k: v for k, v in index.items() if k in filter_set}

        return {
            'success': True,
            'emails_by_recipient': index,
            'total_fetched': total_fetched,
            'truncated': truncated,
            'from_cache': False
        }

    except Exception as e:
        error(logger, "Error listing Resend emails", exc_info=e)
        return {'success': False, 'error': str(e)}
