from typing import Dict, Any
import uuid
from datetime import datetime
import os
import pytz
import requests
import resend
from ratelimiter import RateLimiter
from db.db import get_db
from common.log import get_logger
from common.utils.slack import send_slack

logger = get_logger(__name__)

# Rate limiter for contact form submissions (5 submissions per minute per IP)
@RateLimiter(max_calls=5, period=60)
def submit_contact_form(
    ip_address: str,
    first_name: str,
    last_name: str,
    email: str,
    organization: str,
    inquiry_type: str,
    message: str,
    receive_updates: bool,
    recaptcha_token: str
) -> Dict[str, Any]:
    """
    Process a contact form submission
    
    Args:
        ip_address: IP address of the submitter for rate limiting
        first_name: First name of the submitter
        last_name: Last name of the submitter
        email: Email address of the submitter
        organization: Organization name
        inquiry_type: Type of inquiry (e.g., 'hackathon', 'sponsor', etc.)
        message: The message content
        receive_updates: Whether the submitter wants to receive updates
        recaptcha_token: Google reCAPTCHA token
        
    Returns:
        Dict containing status of submission
    """
    # Verify reCAPTCHA if os.environ.get('FLASK_ENV') != 'development'
    if not verify_recaptcha(recaptcha_token) and os.environ.get('FLASK_ENV') != 'development':
        logger.warning("reCAPTCHA verification failed for email: %s", email)
        return {"success": False, "error": "reCAPTCHA verification failed"}
    
    # Create contact submission record
    contact_id = str(uuid.uuid4())
    timestamp = _get_current_timestamp()
    
    contact_data = {
        'id': contact_id,
        'firstName': first_name,
        'lastName': last_name,
        'email': email,
        'organization': organization,
        'inquiryType': inquiry_type,
        'message': message,
        'receiveUpdates': receive_updates,
        'timestamp': timestamp,
        'ip_address': ip_address,
        'status': 'new'
    }
    
    # Save to database
    db = get_db()
    db.collection('contact_submissions').document(contact_id).set(contact_data)
    logger.info("Saved contact form from %s with ID %s", email, contact_id)
    
    # Send confirmation email
    try:
        send_confirmation_email(first_name, last_name, email)
        logger.info("Sent confirmation email to %s", email)
    except Exception as e:
        logger.error("Failed to send confirmation email to %s: %s", email, str(e))
    
    # Send Slack notification
    try:
        send_slack_notification(contact_data)
        logger.info("Sent Slack notification for contact form %s", contact_id)
    except Exception as e:
        logger.error("Failed to send Slack notification: %s", str(e))
    
    return {"success": True, "id": contact_id}

def _get_current_timestamp() -> str:
    """Get current ISO timestamp in Arizona timezone."""
    az_timezone = pytz.timezone('US/Arizona')
    return datetime.now(az_timezone).isoformat()

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
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        return result.get("success", False)
    except Exception as e:
        logger.exception("Error verifying recaptcha: %s", str(e))
        return False

def send_confirmation_email(first_name: str, last_name: str, email: str) -> bool:
    """
    Send a confirmation email to the contact form submitter.
    
    Args:
        first_name: First name of the recipient
        last_name: Last name of the recipient
        email: Email address to send confirmation to
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    # Set Resend API key
    resend_api_key = os.environ.get('RESEND_WELCOME_EMAIL_KEY')
    if not resend_api_key:
        logger.error("RESEND_WELCOME_EMAIL_KEY not set")
        return False
    
    resend.api_key = resend_api_key
    
    try:
        params = {
            "from": "Opportunity Hack <noreply@opportunityhack.io>",
            "to": [email],
            "subject": "Thank you for contacting Opportunity Hack",
            "html": f"""
            <div>
                <h2>Thank you for contacting Opportunity Hack!</h2>
                <p>Hello {first_name} {last_name},</p>
                <p>We've received your message and will get back to you as soon as possible.</p>
                <p>The Opportunity Hack Team</p>
            </div>
            """
        }
        
        resend.Emails.send(params)
        return True
    except Exception as e:
        logger.error("Error sending email via Resend: %s", str(e))
        return False

def send_slack_notification(contact_data: Dict[str, Any]) -> bool:
    """
    Send a notification to the #contact-us Slack channel.
    
    Args:
        contact_data: The contact form submission data
        
    Returns:
        True if notification was sent successfully, False otherwise
    """
    first_name = contact_data.get('firstName', '')
    last_name = contact_data.get('lastName', '')
    email = contact_data.get('email', '')
    organization = contact_data.get('organization', '')
    inquiry_type = contact_data.get('inquiryType', '')
    message = contact_data.get('message', '')
    contact_id = contact_data.get('id', '')
    
    slack_message = f"""
New contact form submission:
*ID:* {contact_id}
*Name:* {first_name} {last_name}
*Email:* {email}
*Organization:* {organization}
*Inquiry Type:* {inquiry_type}
*Wants Updates:* {"Yes" if contact_data.get('receiveUpdates', False) else "No"}

*Message:*
```
{message}
```
"""
    
    try:
        send_slack(
            message=slack_message,
            channel="contact-us",
            icon_emoji=":email:",
            username="Contact Form Bot"
        )
        return True
    except Exception as e:
        logger.error("Error sending Slack notification: %s", str(e))
        return False