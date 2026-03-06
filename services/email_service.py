import os
import random
from datetime import datetime

import resend
import requests
from ratelimit import limits

from common.log import get_logger, debug
from common.utils.slack import send_slack
from common.utils import safe_get_env_var

logger = get_logger("email_service")

ONE_MINUTE = 60

resend_api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")
if not resend_api_key:
    logger.error("RESEND_WELCOME_EMAIL_KEY not set")
else:
    resend.api_key = resend_api_key

google_recaptcha_key = safe_get_env_var("GOOGLE_CAPTCHA_SECRET_KEY")


def add_utm(url, source="email", medium="welcome", campaign="newsletter_signup", content=None):
    utm_string = f"utm_source={source}&utm_medium={medium}&utm_campaign={campaign}"
    if content:
        utm_string += f"&utm_content={content}"
    return f"{url}?{utm_string}"


def send_nonprofit_welcome_email(organization_name, contact_name, email):
    resend.api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")

    subject = "Welcome to Opportunity Hack: Tech Solutions for Your Nonprofit!"

    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"nonprofit_header_image_{image_number}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to Opportunity Hack</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=f'nonprofit_header_image_{image_number}')}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">

        <h1 style="color: #0088FE;">Welcome {organization_name} to Opportunity Hack!</h1>

        <p>Dear {contact_name},</p>

        <p>We're excited to welcome {organization_name} to the Opportunity Hack community! We're here to connect your nonprofit with skilled tech volunteers to bring your ideas to life.</p>

        <h2 style="color: #0088FE;">What's Next?</h2>
        <ul>
            <li><a href="{add_utm('https://ohack.dev/office-hours', content=image_utm_content)}">Join our weekly Office Hours</a> - Get your questions answered</li>
            <li><a href="{add_utm('https://ohack.dev/about/process', content=image_utm_content)}">Understanding Our Process</a> - Learn how we match you with volunteers</li>
            <li><a href="{add_utm('https://ohack.dev/nonprofits', content=image_utm_content)}">Explore Nonprofit Projects</a> - See what we've worked on</li>
        </ul>

        <h2 style="color: #0088FE;">Important Links:</h2>
        <ul>
            <li><a href="{add_utm('https://www.ohack.dev/about/success-stories', content=image_utm_content)}">Success Stories</a> - See how other nonprofits have benefited</li>
            <li><a href="{add_utm('https://www.ohack.dev/hack', content=image_utm_content)}">Upcoming Hackathons and Events</a></li>
        </ul>

        <p>Questions or need assistance? Reach out on our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack channel</a> or email us at support@ohack.org.</p>

        <p>We're excited to work with you to create tech solutions that amplify your impact!</p>

        <p>Best regards,<br>The Opportunity Hack Team</p>

        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """

    if organization_name is None or organization_name == "" or organization_name == "Unassigned" or organization_name.isspace():
        organization_name = "Nonprofit Partner"

    if contact_name is None or contact_name == "" or contact_name == "Unassigned" or contact_name.isspace():
        contact_name = "Nonprofit Friend"

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{contact_name} <{email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": subject,
        "html": html_content,
    }
    logger.info(f"Sending nonprofit application email to {email}")

    email = resend.Emails.SendParams(params)
    resend.Emails.send(email)

    logger.info(f"Sent nonprofit application email to {email}")
    return True


def send_welcome_email(name, email):
    resend.api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")

    subject = "Welcome to Opportunity Hack: Code for Good!"

    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"header_image_{image_number}"


    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to Opportunity Hack</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=f'header_image_{image_number}')}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">

        <h1 style="color: #0088FE;">Hey {name}!! Welcome to Opportunity Hack!</h1>

        <p>We're thrilled you've joined our community of tech volunteers making a difference!</p>

        <p>At Opportunity Hack, we believe in harnessing the power of code for social good. Our mission is simple: connect skilled volunteers like you with nonprofits that need tech solutions.</p>

        <h2 style="color: #0088FE;">Ready to dive in?</h2>
        <ul>
            <li><a href="{add_utm('https://ohack.dev/nonprofits', content=image_utm_content)}">Explore Nonprofit Projects</a></li>
            <li><a href="{add_utm('https://ohack.dev/about/hearts', content=image_utm_content)}">Learn about our Hearts System</a></li>
            <li><a href="{add_utm('https://ohack.dev/office-hours', content=image_utm_content)}">Join our weekly Office Hours</a></li>
            <li><a href="{add_utm('https://ohack.dev/profile', content=image_utm_content)}">Update your profile</a></li>
            <li><a href="{add_utm('https://github.com/opportunity-hack/frontend-ohack.dev/issues', content=image_utm_content)}">Jump in: check out our open GitHub Issues</a></li>
        </ul>

        <p>Got questions? Reach out on our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack channel</a>.</p>

        <p>Together, we can code for change!</p>

        <p>The Opportunity Hack Team</p>

        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """


    if name is None or name == "" or name == "Unassigned" or name.isspace():
        name = "OHack Friend"


    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{name} <{email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": subject,
        "html": html_content,
    }

    email = resend.Emails.SendParams(params)
    resend.Emails.send(email)
    debug(logger, "Processing email", email=email)
    return True


def send_welcome_emails():
    from db.db import get_db
    logger.info("Sending welcome emails")
    db = get_db()

    query = db.collection('leads').stream()
    leads = []
    for lead in query:
        lead_dict = lead.to_dict()
        if "welcome_email_sent" not in lead_dict and "email" in lead_dict and lead_dict["email"] is not None and lead_dict["email"] != "":
            leads.append(lead)

    send_email = False

    emails = set()

    for lead in leads:
        lead_dict = lead.to_dict()
        email = lead_dict["email"].lower()
        if email in emails:
            logger.info(f"Skipping duplicate email {email}")
            continue

        logger.info(f"Sending welcome email to '{lead_dict['name']}' {email} for {lead.id}")

        if send_email:
            success_send_email = send_welcome_email(lead_dict["name"], email)
            if success_send_email:
                logger.info(f"Sent welcome email to {email}")
                lead.reference.update({
                    "welcome_email_sent": datetime.now().isoformat()
                })
        emails.add(email)


async def save_lead(json):
    from db.db import get_db
    token = json["token"]

    if "name" not in json or "email" not in json:
        logger.error(f"Missing field name or email {json}")
        return False

    if len(json["name"]) < 2 or len(json["email"]) < 3:
        logger.error(f"Name or email too short name:{json['name']} email:{json['email']}")
        return False

    recaptcha_response = requests.post(
        f"https://www.google.com/recaptcha/api/siteverify?secret={google_recaptcha_key}&response={token}")
    recaptcha_response_json = recaptcha_response.json()
    logger.info(f"Recaptcha Response: {recaptcha_response_json}")

    if recaptcha_response_json["success"] == False:
        return False
    else:
        logger.info("Recaptcha Success, saving...")
        db = get_db()
        collection = db.collection('leads')
        del json["token"]

        json["timestamp"] = datetime.now().isoformat()
        insert_res = collection.add(json)
        logger.info(f"Lead saved for {json}")

        slack_message = f"New lead! Name:`{json['name']}` Email:`{json['email']}`"
        send_slack(slack_message, "ohack-dev-leads")

        success_send_email = send_welcome_email( json["name"], json["email"] )
        if success_send_email:
            logger.info(f"Sent welcome email to {json['email']}")
            collection.document(insert_res[1].id).update({
                "welcome_email_sent": datetime.now().isoformat()
            })
        return True


@limits(calls=30, period=ONE_MINUTE)
async def save_lead_async(json):
    await save_lead(json)
