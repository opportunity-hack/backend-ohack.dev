from typing import Dict, List, Optional, Union, Any
import uuid
from datetime import datetime
import pytz
from functools import lru_cache
from ratelimiter import RateLimiter
from db.db import get_db
from google.cloud import firestore
from common.utils.slack import get_slack_user_by_email, send_slack
from common.log import get_logger, info, debug, warning, error, exception
from common.utils.redis_cache import redis_cached, delete_cached, clear_pattern
import os
import requests
import resend

logger = get_logger("services.volunteers_service")

def _generate_volunteer_id() -> str:
    """Generate a unique ID for a volunteer."""
    return str(uuid.uuid4())

def _get_current_timestamp() -> str:
    """Get current ISO timestamp in Arizona timezone."""
    az_timezone = pytz.timezone('US/Arizona')
    return datetime.now(az_timezone).isoformat()

@redis_cached(prefix="volunteer:by_user_id", ttl=10)
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

@redis_cached(prefix="volunteer:by_email", ttl=10)
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

@redis_cached(prefix="volunteer:by_event", ttl=120)
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
        
        # Generate volunteer type specific content
        volunteer_specific_content = ""
        if volunteer_type.lower() == "mentor":
            volunteer_specific_content = f"""
            <div style="background-color: #e8f5e8; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #27ae60;">
                <h3 style="color: #27ae60; margin-top: 0;">🚀 Next Steps for Mentors</h3>
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
                <h3 style="color: #856404; margin-top: 0;">⚖️ Information for Judges</h3>
                <p style="margin-bottom: 15px;">Learn about the judging process, evaluation criteria, and what to expect:</p>
                <div style="text-align: center;">
                    <a href="https://www.ohack.dev/about/judges" 
                       style="background-color: #ffc107; color: #212529; padding: 12px 25px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
                        📋 Judge Guidelines & Information
                    </a>
                </div>
            </div>"""
        
        params = {
            "from": "Opportunity Hack <welcome@apply.ohack.dev>",
            "to": [email],            
            "subject": f"Thank you for volunteering as an Opportunity Hack {volunteer_type_readable}",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border-radius: 5px;">
                <div style="text-align: center; margin-bottom: 20px;">
                    <img src="https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Dark_Blue.png" alt="Opportunity Hack Logo" style="max-width: 150px;">
                </div>
                <h2 style="color: #3498db; text-align: center;">Thank you for volunteering with Opportunity Hack!</h2>
                <p style="font-size: 16px;">Hello {full_name},</p>
                <p style="font-size: 16px;">Thank you for signing up as a <strong>{volunteer_type_readable}</strong> for Opportunity Hack. 
                We've received your information and our team will review it shortly.</p>
                {calendar_note}
                {volunteer_specific_content}
                <p style="font-size: 16px;">We'll be in touch with next steps.</p>
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
            "from": "Opportunity Hack <welcome@apply.ohack.dev>",
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
    Send a notification to Slack when a volunteer form is submitted or updated.
    
    Args:
        volunteer_data: The volunteer data
        is_update: Whether this is an update to an existing volunteer
        
    Returns:
        True if notification was sent successfully, False otherwise
    """
    action_type = "updated" if is_update else "submitted"
    first_name = volunteer_data.get('firstName', '')
    last_name = volunteer_data.get('lastName', '')
    name = volunteer_data.get('name', '')


    email = volunteer_data.get('email', '')
    volunteer_type = volunteer_data.get('volunteer_type', '')
    event_id = volunteer_data.get('event_id', '')
    
    slack_message = f"""
New volunteer form {action_type}:
*Name:* {name} {first_name} {last_name}
*Email:* {email}
*Type:* {volunteer_type}
*Event ID:* {event_id}
"""
    
    try:
        send_slack(
            message=slack_message,
            channel="volunteer-applications",
            icon_emoji=":raising_hand:",
            username="Volunteer Bot"
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
    
    return [hacker.to_dict() for hacker in hackers] if hackers else []


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
        'isCheckedIn': True,
        'checkInTime': current_time,
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

Teams needing help in these areas can reach out to directly or #ask-a-mentor so everyone can benefit from their expertise.
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


def send_volunteer_message(
    volunteer_id: str, 
    message: str, 
    admin_user_id: str,
    admin_user: Any = None,
    recipient_type: str = 'volunteer',
    recipient_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send a message to a volunteer via Slack and email.
    
    Args:
        volunteer_id: The ID of the volunteer to message
        message: The message content to send
        admin_user_id: The user ID of the admin sending the message
        recipient_type: Type of recipient (mentor, sponsor, judge, volunteer, hacker, etc.)
        recipient_id: Optional specific recipient ID for enhanced context
        
    Returns:
        Dict containing delivery status and volunteer information
    """
    from db.db import get_db
    import html
    
    try:
        # Get volunteer by ID from the volunteers collection
        db = get_db()
        volunteer_doc = db.collection('volunteers').document(volunteer_id).get()
        
        if not volunteer_doc.exists:
            return {
                'success': False,
                'error': 'Volunteer not found',
                'volunteer_id': volunteer_id
            }
        
        volunteer = volunteer_doc.to_dict()
        
        # Extract contact information from volunteer data
        email = volunteer.get('email')
        slack_user_id = volunteer.get('slack_user_id')
        name = volunteer.get('name', 'Volunteer')
        volunteer_type = volunteer.get('volunteer_type', recipient_type)
        
        if not email:
            return {
                'success': False,
                'error': 'Volunteer email not found',
                'volunteer_id': volunteer_id
            }
        
        # Track message delivery status
        delivery_status = {
            'slack_sent': False,
            'email_sent': False,
            'slack_error': None,
            'email_error': None
        }
        
        # Send Slack message if slack_user_id is available
        if slack_user_id:
            try:
                # Enhanced Slack message with context
                slack_message = f"📧 *Message from Opportunity Hack Team*\n\n{message}\n\n_This message was sent to you as a registered {volunteer_type.title()} for Opportunity Hack._"
                send_slack(message=slack_message, channel=slack_user_id)
                delivery_status['slack_sent'] = True
                info(logger, "Slack message sent to volunteer", 
                     volunteer_id=volunteer_id, slack_user_id=slack_user_id, recipient_type=recipient_type)
            except Exception as slack_error:
                delivery_status['slack_error'] = str(slack_error)
                error(logger, "Failed to send Slack message to volunteer", 
                      volunteer_id=volunteer_id, exc_info=slack_error)
        
        # Send email message using the resend service
        try:
            # Configure resend
            resend.api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
            
            # Escape HTML characters and convert newlines to <br> tags
            escaped_message = html.escape(message)
            formatted_message = escaped_message.replace('\n', '<br>')
            
            # Enhanced subject line based on recipient type
            subject_map = {
                'mentor': 'Message for Mentors - Opportunity Hack',
                'sponsor': 'Message for Sponsors - Opportunity Hack',
                'judge': 'Message for Judges - Opportunity Hack',
                'hacker': 'Message for Participants - Opportunity Hack',
                'volunteer': 'Message from Opportunity Hack Team'
            }
            email_subject = subject_map.get(recipient_type.lower(), 'Message from Opportunity Hack Team')
            
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
                
                <!-- Social Media Footer -->
                <div style="background-color: #f8f9fa; padding: 20px; margin: 20px 0; border-radius: 8px; text-align: center;">
                    <h3 style="color: #2c3e50; margin-bottom: 15px; font-size: 16px;">🌟 Stay Connected with Opportunity Hack!</h3>
                    <p style="margin-bottom: 15px; color: #34495e;">Follow us for updates, volunteer opportunities, and inspiring stories from our community:</p>
                    <div style="margin: 15px 0;">
                        <a href="https://www.instagram.com/opportunityhack/" style="text-decoration: none; margin: 0 8px; color: #E4405F;">📸 Instagram</a> |
                        <a href="https://www.facebook.com/OpportunityHack/" style="text-decoration: none; margin: 0 8px; color: #1877F2;">👥 Facebook</a> |
                        <a href="https://www.linkedin.com/company/opportunity-hack/" style="text-decoration: none; margin: 0 8px; color: #0A66C2;">💼 LinkedIn</a> |
                        <a href="https://www.threads.net/@opportunityhack" style="text-decoration: none; margin: 0 8px; color: #000;">🧵 Threads</a>
                    </div>
                    <div style="margin: 10px 0;">
                        <a href="https://opportunity-hack.slack.com" style="text-decoration: none; margin: 0 8px; color: #4A154B;">💬 Join Slack</a> |
                        <a href="https://twitter.com/opportunityhack" style="text-decoration: none; margin: 0 8px; color: #1DA1F2;">🐦 Twitter</a> |
                        <a href="https://github.com/opportunity-hack/" style="text-decoration: none; margin: 0 8px; color: #333;">💻 GitHub</a>
                    </div>
                    <p style="font-size: 12px; color: #666; margin-top: 15px;">Help us make a bigger impact - share our mission with your network! 🚀</p>
                </div>
                
                <hr>
                <p style="font-size: 12px; color: #666;">
                    This message was sent to you as a registered {volunteer_type.title()} for Opportunity Hack.
                </p>
                """
            
            # Send email
            params = {
                "from": "Opportunity Hack <welcome@apply.ohack.dev>",
                "to": [email],
                "reply_to": "Opportunity Hack Questions <questions@ohack.org>",
                "subject": email_subject,
                "html": html_content,
            }
            
            email_result = resend.Emails.send(params)
            delivery_status['email_sent'] = True
            info(logger, "Email sent to volunteer", 
                 volunteer_id=volunteer_id, email=email, result=email_result, recipient_type=recipient_type)
            
        except Exception as email_error:
            delivery_status['email_error'] = str(email_error)
            error(logger, "Failed to send email to volunteer", 
                  volunteer_id=volunteer_id, exc_info=email_error)
        
        # Update volunteer document with message tracking
        try:
            # Combine first_name and last_name for full name, don't use get but use .first_name and .last_name directly
            admin_full_name = f"{admin_user.first_name} {admin_user.last_name}" if admin_user else "Admin"
           
            message_record = {                
                'subject': email_subject,
                'timestamp': _get_current_timestamp(),
                'sent_by': admin_full_name,
                'recipient_type': recipient_type,
                'delivery_status': delivery_status
            }
            
            # Add the message to the volunteer's messages_sent array
            volunteer_ref = db.collection('volunteers').document(volunteer_id)
            volunteer_ref.update({
                'messages_sent': firestore.ArrayUnion([message_record]),
                'last_message_timestamp': _get_current_timestamp(),
                'updated_timestamp': _get_current_timestamp()
            })                    
            user_id = volunteer.get('user_id', '')

            info(logger, "Updated volunteer document with message tracking", 
                 volunteer_id=volunteer_id, message_timestamp=message_record['timestamp'])
            
            _clear_volunteer_caches(
                user_id=user_id,
                email=email,
                event_id=volunteer.get('event_id', ''),
                volunteer_type=volunteer_type
            )
                 
        except Exception as tracking_error:
            error(logger, "Failed to update volunteer document with message tracking", 
                  volunteer_id=volunteer_id, exc_info=tracking_error)
        
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
