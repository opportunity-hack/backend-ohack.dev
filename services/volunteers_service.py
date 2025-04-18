from typing import Dict, List, Optional, Union, Any
import uuid
from datetime import datetime
import pytz
from functools import lru_cache
from cachetools import TTLCache
from cachetools.func import ttl_cache
from ratelimiter import RateLimiter
from db.db import get_db
from common.utils.slack import get_slack_user_by_email, send_slack
from common.log import get_logger
import os
import requests
import resend

logger = get_logger(__name__)

# Cache for volunteers (10 minute TTL)
volunteers_cache = TTLCache(maxsize=1000, ttl=600)

def _generate_volunteer_id() -> str:
    """Generate a unique ID for a volunteer."""
    return str(uuid.uuid4())

def _get_current_timestamp() -> str:
    """Get current ISO timestamp in Arizona timezone."""
    az_timezone = pytz.timezone('US/Arizona')
    return datetime.now(az_timezone).isoformat()

@ttl_cache(maxsize=100, ttl=10)
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

@ttl_cache(maxsize=100, ttl=10)
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
    # Clear manual cache
    cache_key = f"{user_id}_{event_id}_{volunteer_type}"
    if cache_key in volunteers_cache:
        del volunteers_cache[cache_key]
        
    # Clear ttl_cache for get_volunteer_by_user_id
    get_volunteer_by_user_id.cache_clear()
    
    # Clear ttl_cache for get_volunteer_by_email
    get_volunteer_by_email.cache_clear()
    
    logger.debug(f"Cleared caches for volunteer: {email}, event: {event_id}, type: {volunteer_type}")

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
            "from": "Opportunity Hack <noreply@opportunityhack.io>",
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
            "from": "Opportunity Hack <noreply@opportunityhack.io>",
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
    email = volunteer_data.get('email', '')
    volunteer_type = volunteer_data.get('volunteer_type', '')
    event_id = volunteer_data.get('event_id', '')
    
    slack_message = f"""
New volunteer form {action_type}:
*Name:* {first_name} {last_name}
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