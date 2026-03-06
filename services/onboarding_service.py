from datetime import datetime

import pytz
from ratelimit import limits

from common.log import get_logger, debug, error
from db.db import get_db
from api.messages.message import Message

logger = get_logger("onboarding_service")

ONE_MINUTE = 60


@limits(calls=50, period=ONE_MINUTE)
def save_onboarding_feedback(json_data):
    """
    Save or update onboarding feedback to Firestore.

    Handles the data format and implements logic to either:
    1. Create new feedback if no existing feedback is found
    2. Update existing feedback if found based on contact info or client info
    """
    db = get_db()
    logger.info("Processing onboarding feedback submission")
    debug(logger, "Onboarding feedback data", data=json_data)

    try:
        contact_info = json_data.get("contactForFollowup", {})
        client_info = json_data.get("clientInfo", {})

        has_contact_info = (
            contact_info.get("name") and
            contact_info.get("email") and
            contact_info.get("name").strip() and
            contact_info.get("email").strip()
        )

        existing_feedback = None

        if has_contact_info:
            logger.info("Searching for existing feedback by contact info")
            existing_docs = db.collection('onboarding_feedbacks').where(
                "contactForFollowup.name", "==", contact_info["name"]
            ).where(
                "contactForFollowup.email", "==", contact_info["email"]
            ).limit(1).stream()

            for doc in existing_docs:
                existing_feedback = doc
                logger.info(f"Found existing feedback by contact info: {doc.id}")
                break
        else:
            logger.info("Searching for existing feedback by client info")
            existing_docs = db.collection('onboarding_feedbacks').where(
                "clientInfo.userAgent", "==", client_info.get("userAgent", "")
            ).where(
                "clientInfo.ipAddress", "==", client_info.get("ipAddress", "")
            ).limit(1).stream()

            for doc in existing_docs:
                existing_feedback = doc
                logger.info(f"Found existing feedback by client info: {doc.id}")
                break

        feedback_data = {
            "overallRating": json_data.get("overallRating"),
            "usefulTopics": json_data.get("usefulTopics", []),
            "missingTopics": json_data.get("missingTopics", ""),
            "easeOfUnderstanding": json_data.get("easeOfUnderstanding", ""),
            "improvements": json_data.get("improvements", ""),
            "additionalFeedback": json_data.get("additionalFeedback", ""),
            "contactForFollowup": contact_info,
            "clientInfo": client_info,
            "timestamp": datetime.now(pytz.utc)
        }

        if existing_feedback:
            logger.info(f"Updating existing feedback: {existing_feedback.id}")
            existing_feedback.reference.update(feedback_data)
            message = "Onboarding feedback updated successfully"
        else:
            logger.info("Creating new onboarding feedback")
            db.collection('onboarding_feedbacks').add(feedback_data)
            message = "Onboarding feedback submitted successfully"

        return Message(message)

    except Exception as e:
        error(logger, f"Error saving onboarding feedback: {str(e)}", exc_info=True)
        return Message("Failed to save onboarding feedback", status="error")
