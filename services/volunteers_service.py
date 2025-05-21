from typing import Dict, List, Optional, Union, Any
import uuid
from datetime import datetime
import pytz
from functools import lru_cache
from ratelimiter import RateLimiter
from db.db import get_db
from common.utils.slack import get_slack_user_by_email, send_slack
from common.log import get_logger
from common.utils.redis_cache import redis_cached, delete_cached, clear_pattern
import os
import requests
import resend

logger = get_logger(__name__)

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
    # Clear cache for specific user lookup
    user_key = f"volunteer:by_user_id:{user_id}:{event_id}:{volunteer_type}"
    delete_cached(user_key)
    
    # Clear cache for specific email lookup
    email_key = f"volunteer:by_email:{email}:{event_id}:{volunteer_type}"
    delete_cached(email_key)
    
    # Clear event-based volunteer caches
    event_key = f"volunteer:by_event:{event_id}:{volunteer_type}"
    clear_pattern(f"{event_key}*")
    
    logger.debug(f"Cleared caches for volunteer: {email}, event: {event_id}, type: {volunteer_type}")

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
        logger.warning("No GOOGLE_CAPTCHA_SECRET_KEY set, skipping verification")
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
        logger.exception(f"Error verifying recaptcha: {e}")
        return False

def send_volunteer_confirmation_email(first_name: str, last_name: str, email: str, volunteer_type: str) -> bool:
    """
    Send a confirmation email to the volunteer who submitted the form.
    
    Args:
        first_name: First name of the volunteer
        last_name: Last name of the volunteer
        email: Email address of the volunteer
        volunteer_type: Type of volunteer (mentor, sponsor, judge)
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
        return False
    
    resend.api_key = resend_api_key
    
    try:
        volunteer_type_readable = volunteer_type.capitalize()
        params = {
            "from": "Opportunity Hack <welcome@apply.ohack.dev>",
            "to": [email],
            "subject": f"Thank you for volunteering as an Opportunity Hack {volunteer_type_readable}",
            "html": f"""
            <div>
                <h2>Thank you for volunteering with Opportunity Hack!</h2>
                <p>Hello {first_name} {last_name},</p>
                <p>Thank you for signing up as a {volunteer_type_readable} for Opportunity Hack. 
                   We've received your information and our team will review it shortly.</p>
                <p>We'll be in touch with next steps.</p>
                <p>The Opportunity Hack Team</p>
            </div>
            """
        }
        
        resend.Emails.send(params)
        logger.info(f"Sent confirmation email to volunteer {email}")
        return True
    except Exception as e:
        logger.error(f"Error sending email via Resend: {str(e)}")
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
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
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
        logger.info(f"Sent admin notification email about volunteer {email}")
        return True
    except Exception as e:
        logger.error(f"Error sending admin email via Resend: {str(e)}")
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
        logger.info(f"Sent Slack notification about volunteer {email}")
        return True
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False

def create_or_update_volunteer(
    user_id: str,
    event_id: str,
    email: str,
    volunteer_data: Dict[str, Any],
    created_by: Optional[str] = None,
    recaptcha_token: Optional[str] = None
) -> Dict[str, Any]:
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
            logger.warning(f"reCAPTCHA verification failed for email: {email}")
            return {"error": "reCAPTCHA verification failed"}
    
    db = get_db()
    volunteer_type = volunteer_data.get('volunteer_type')
    
    # Check if volunteer already exists
    existing = get_volunteer_by_user_id(user_id, event_id, volunteer_type)
    
    if existing:
        # Update existing record
        volunteer_id = existing.get('id')
        volunteer_ref = db.collection('volunteers').document(volunteer_id)
        
        # Update with new data
        update_data = {**volunteer_data}
        update_data['updated_by'] = user_id
        update_data['updated_timestamp'] = _get_current_timestamp()
        
        volunteer_ref.update(update_data)
        
        # Clear all related caches
        _clear_volunteer_caches(user_id, email, event_id, volunteer_type)
        
        # Send admin notification about the update
        try:
            send_admin_notification_email({**existing, **update_data}, is_update=True)
            send_slack_volunteer_notification({**existing, **update_data}, is_update=True)
            logger.info(f"Sent notifications about updated volunteer {email}")
        except Exception as e:
            logger.error(f"Failed to send notifications for updated volunteer {email}: {str(e)}")
        
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
            logger.warning(f"Could not get slack user for {email}: {str(e)}")
        
        # Save to database
        db.collection('volunteers').document(volunteer_id).set(volunteer_doc)
        
        # Clear all related caches 
        _clear_volunteer_caches(user_id, email, event_id, volunteer_type)
        
        # Send confirmation email to the volunteer
        try:
            first_name = volunteer_doc.get('firstName', '')
            last_name = volunteer_doc.get('lastName', '')
            send_volunteer_confirmation_email(first_name, last_name, email, volunteer_type)
            send_admin_notification_email(volunteer_doc)
            send_slack_volunteer_notification(volunteer_doc)
            logger.info(f"Sent notifications for new volunteer {email}")
        except Exception as e:
            logger.error(f"Failed to send notifications for new volunteer {email}: {str(e)}")
        
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
        logger.error(f"Failed to send check-in notification: {str(e)}")
    
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
            logger.error(f"Error calculating check-in duration: {str(e)}")
    
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
        logger.error(f"Failed to send check-out notification: {str(e)}")
    
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
            channel="mentor-checkin",
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
            channel="mentor-checkin",
            icon_emoji=":v:",
            username="Mentor Check-in"
        )
        logger.info(f"Sent Slack notification about mentor check-out for {volunteer.get('email')}")
        return True
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False