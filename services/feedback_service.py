import os
import uuid
from datetime import datetime

import resend
from cachetools import cached, TTLCache
from ratelimit import limits
from firebase_admin import firestore

from common.log import get_logger
from common.utils.slack import send_slack_audit, send_slack
from db.db import get_db
from services.users_service import get_slack_user_from_propel_user_id, get_user_from_slack_id
from api.messages.message import Message

logger = get_logger("feedback_service")

ONE_MINUTE = 60

resend_api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")
if resend_api_key:
    resend.api_key = resend_api_key


@limits(calls=50, period=ONE_MINUTE)
def save_feedback(propel_user_id, json):
    db = get_db()
    logger.info("Saving Feedback")
    send_slack_audit(action="save_feedback", message="Saving", payload=json)

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    user_db_id = get_user_from_slack_id(slack_user["sub"]).id
    feedback_giver_id = slack_user["sub"]

    doc_id = uuid.uuid1().hex
    feedback_receiver_id = json.get("feedback_receiver_id")
    relationship = json.get("relationship")
    duration = json.get("duration")
    confidence_level = json.get("confidence_level")
    is_anonymous = json.get("is_anonymous", False)
    feedback_data = json.get("feedback", {})

    collection = db.collection('feedback')

    insert_res = collection.document(doc_id).set({
        "feedback_giver_slack_id": feedback_giver_id,
        "feedback_giver_id": user_db_id,
        "feedback_receiver_id": feedback_receiver_id,
        "relationship": relationship,
        "duration": duration,
        "confidence_level": confidence_level,
        "is_anonymous": is_anonymous,
        "feedback": feedback_data,
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Insert Result: {insert_res}")

    notify_feedback_receiver(feedback_receiver_id)

    get_user_feedback.cache_clear()

    return Message("Feedback saved successfully")


def notify_feedback_receiver(feedback_receiver_id):
    db = get_db()
    user_doc = db.collection('users').document(feedback_receiver_id).get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        user_email = user_data.get('email_address', '')
        slack_user_id = user_data.get('user_id', '').split('-')[-1]
        logger.info(f"User with ID {feedback_receiver_id} found")
        logger.info(f"Sending notification to user {slack_user_id}")

        message = (
            f"Hello <@{slack_user_id}>! You've received new feedback. "
            "Visit https://www.ohack.dev/myfeedback to view it."
        )

        send_slack(message=message, channel=slack_user_id)
        logger.info(f"Notification sent to user {slack_user_id}")

        if user_email:
            subject = "New Feedback Received"
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>New Feedback Received</title>
            </head>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
                <h1 style="color: #0088FE;">New Feedback Received</h1>
                <p>Hello,</p>
                <p>You have received new feedback. Please visit <a href="https://www.ohack.dev/myfeedback">My Feedback</a> to view it.</p>
                <p>Thank you for being a part of the Opportunity Hack community!</p>
                <p>Best regards,<br>The Opportunity Hack Team</p>
            </body>
            </html>
            """
            params = {
                "from": "Opportunity Hack <welcome@notifs.ohack.org>",
                "to": f"{user_data.get('name', 'User')} <{user_email}>",
                "bcc": "greg@ohack.org",
                "subject": subject,
                "html": html_content,
            }
            email = resend.Emails.SendParams(params)
            resend.Emails.send(email)
            logger.info(f"Email notification sent to {user_email}")
    else:
        logger.warning(f"User with ID {feedback_receiver_id} not found")


@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=100, period=ONE_MINUTE)
def get_user_feedback(propel_user_id):
    logger.info(f"Getting feedback for propel_user_id: {propel_user_id}")
    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    db_user_id = get_user_from_slack_id(slack_user["sub"]).id

    feedback_docs = db.collection('feedback').where("feedback_receiver_id", "==", db_user_id).order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    feedback_list = []
    for doc in feedback_docs:
        feedback = doc.to_dict()
        if feedback.get("is_anonymous", False):
            if "feedback_giver_id" in feedback:
                feedback.pop("feedback_giver_id")
            if "feedback_giver_slack_id" in feedback:
                feedback.pop("feedback_giver_slack_id")
        feedback_list.append(feedback)

    return {"feedback": feedback_list}
