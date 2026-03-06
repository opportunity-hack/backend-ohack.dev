import uuid
import os
import random
from datetime import datetime, timedelta

from cachetools import cached, TTLCache
from ratelimit import limits
from firebase_admin import firestore
from firebase_admin.firestore import DocumentReference, DocumentSnapshot
import resend

from common.log import get_logger
from common.utils.slack import send_slack_audit, send_slack, async_send_slack, invite_user_to_channel
from common.utils.firebase import (
    get_hackathon_by_event_id,
    get_volunteer_from_db_by_event,
    get_volunteer_checked_in_from_db_by_event,
)
from common.utils.validators import validate_hackathon_data
from common.utils.firestore_helpers import (
    doc_to_json,
    doc_to_json_recursive,
    hash_key,
    log_execution_time,
    clear_all_caches,
    register_cache,
)
from api.messages.message import Message
from services.users_service import get_propel_user_details_by_id

logger = get_logger("hackathons_service")

ONE_MINUTE = 60
THIRTY_SECONDS = 30


def _get_db():
    from db.db import get_db
    return get_db()


def clear_cache():
    """Clear all hackathon-related caches."""
    clear_all_caches()
    get_single_hackathon_event.cache_clear()
    get_single_hackathon_id.cache_clear()
    get_hackathon_list.cache_clear()


def add_nonprofit_to_hackathon(json):
    hackathonId = json["hackathonId"]
    nonprofitId = json["nonprofitId"]

    logger.info(f"Add Nonprofit to Hackathon Start hackathonId={hackathonId} nonprofitId={nonprofitId}")

    db = _get_db()
    hackathon_doc = db.collection('hackathons').document(hackathonId)
    nonprofit_doc = db.collection('nonprofits').document(nonprofitId)
    hackathon_data = hackathon_doc.get()
    if not hackathon_data.exists:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Hackathon not found"
        }
    nonprofit_data = nonprofit_doc.get()
    if not nonprofit_data.exists:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Nonprofit not found"
        }
    hackathon_dict = hackathon_data.to_dict()
    if "nonprofits" not in hackathon_dict:
        hackathon_dict["nonprofits"] = []
    if nonprofit_doc in hackathon_dict["nonprofits"]:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Nonprofit already in hackathon"
        }
    hackathon_dict["nonprofits"].append(nonprofit_doc)
    hackathon_doc.set(hackathon_dict, merge=True)

    clear_cache()

    return {
        "message": "Nonprofit added to hackathon"
    }


def remove_nonprofit_from_hackathon(json):
    hackathonId = json["hackathonId"]
    nonprofitId = json["nonprofitId"]

    logger.info(f"Remove Nonprofit from Hackathon Start hackathonId={hackathonId} nonprofitId={nonprofitId}")

    db = _get_db()
    hackathon_doc = db.collection('hackathons').document(hackathonId)
    nonprofit_doc = db.collection('nonprofits').document(nonprofitId)

    if not hackathon_doc:
        logger.warning(f"Remove Nonprofit from Hackathon End (hackathon not found)")
        return {
            "message": "Hackathon not found"
        }

    hackathon_data = hackathon_doc.get().to_dict()

    if "nonprofits" not in hackathon_data or not hackathon_data["nonprofits"]:
        logger.warning(f"Remove Nonprofit from Hackathon End (no nonprofits in hackathon)")
        return {
            "message": "No nonprofits in hackathon"
        }

    nonprofit_found = False
    updated_nonprofits = []

    for np in hackathon_data["nonprofits"]:
        if np.id != nonprofitId:
            updated_nonprofits.append(np)
        else:
            nonprofit_found = True

    if not nonprofit_found:
        logger.warning(f"Remove Nonprofit from Hackathon End (nonprofit not found in hackathon)")
        return {
            "message": "Nonprofit not found in hackathon"
        }

    hackathon_data["nonprofits"] = updated_nonprofits
    hackathon_doc.set(hackathon_data, merge=True)

    clear_cache()

    logger.info(f"Remove Nonprofit from Hackathon End (nonprofit removed)")
    return {
        "message": "Nonprofit removed from hackathon"
    }


@cached(cache=TTLCache(maxsize=100, ttl=20))
@limits(calls=2000, period=ONE_MINUTE)
def get_single_hackathon_id(id):
    logger.debug(f"get_single_hackathon_id start id={id}")
    db = _get_db()
    doc = db.collection('hackathons').document(id)

    if doc is None:
        logger.warning("get_single_hackathon_id end (no results)")
        return {}
    else:
        result = doc_to_json(docid=doc.id, doc=doc)
        result["id"] = doc.id

        logger.info(f"get_single_hackathon_id end (with result):{result}")
        return result
    return {}


@cached(cache=TTLCache(maxsize=100, ttl=10))
@limits(calls=2000, period=ONE_MINUTE)
def get_volunteer_by_event(event_id, volunteer_type, admin=False):
    logger.debug(f"get {volunteer_type} start event_id={event_id}")

    if event_id is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []

    results = get_volunteer_from_db_by_event(event_id, volunteer_type, admin=admin)

    if results is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []
    else:
        logger.debug(f"get {volunteer_type} end (with result):{results}")
        return results


@cached(cache=TTLCache(maxsize=100, ttl=5))
def get_volunteer_checked_in_by_event(event_id, volunteer_type):
    logger.debug(f"get {volunteer_type} start event_id={event_id}")

    if event_id is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []

    results = get_volunteer_checked_in_from_db_by_event(event_id, volunteer_type)

    if results is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []
    else:
        logger.debug(f"get {volunteer_type} end (with result):{results}")
        return results


@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=2000, period=ONE_MINUTE)
def get_single_hackathon_event(hackathon_id):
    logger.debug(f"get_single_hackathon_event start hackathon_id={hackathon_id}")
    result = get_hackathon_by_event_id(hackathon_id)

    if result is None:
        logger.warning("get_single_hackathon_event end (no results)")
        return {}
    else:
        if "nonprofits" in result and result["nonprofits"]:
            result["nonprofits"] = [doc_to_json(doc=npo, docid=npo.id) for npo in result["nonprofits"]]
        else:
            result["nonprofits"] = []
        if "teams" in result and result["teams"]:
            result["teams"] = [doc_to_json(doc=team, docid=team.id) for team in result["teams"]]
        else:
            result["teams"] = []

        logger.info(f"get_single_hackathon_event end (with result):{result}")
        return result
    return {}


@cached(cache=TTLCache(maxsize=100, ttl=3600), key=lambda is_current_only: str(is_current_only))
@limits(calls=200, period=ONE_MINUTE)
@log_execution_time
def get_hackathon_list(is_current_only=None):
    """
    Retrieve a list of hackathons based on specified criteria.

    Args:
        is_current_only: Filter type - 'current', 'previous', or None for all hackathons

    Returns:
        Dictionary containing list of hackathons with document references resolved
    """
    logger.debug(f"Hackathon List - Getting {is_current_only or 'all'} hackathons")
    db = _get_db()

    query = db.collection('hackathons')
    today_str = datetime.now().strftime("%Y-%m-%d")

    if is_current_only == "current":
        logger.debug(f"Querying current events (end_date >= {today_str})")
        query = query.where(filter=firestore.FieldFilter("end_date", ">=", today_str)).order_by("end_date", direction=firestore.Query.ASCENDING)

    elif is_current_only == "previous":
        target_date = datetime.now() + timedelta(days=-3*365)
        target_date_str = target_date.strftime("%Y-%m-%d")
        logger.debug(f"Querying previous events ({target_date_str} <= end_date <= {today_str})")
        query = query.where("end_date", ">=", target_date_str).where("end_date", "<=", today_str)
        query = query.order_by("end_date", direction=firestore.Query.DESCENDING).limit(50)

    else:
        query = query.order_by("start_date")

    try:
        logger.debug(f"Executing query: {query}")
        docs = query.stream()
        results = _process_hackathon_docs(docs)
        logger.debug(f"Retrieved {len(results)} hackathon results")
        return {"hackathons": results}
    except Exception as e:
        logger.error(f"Error retrieving hackathons: {str(e)}")
        return {"hackathons": [], "error": str(e)}


def _process_hackathon_docs(docs):
    """
    Process hackathon documents and resolve references.
    """
    if not docs:
        return []

    results = []
    for doc in docs:
        try:
            d = doc_to_json(doc.id, doc)

            for key, value in d.items():
                if isinstance(value, list):
                    d[key] = [doc_to_json_recursive(item) for item in value]
                elif isinstance(value, (DocumentReference, DocumentSnapshot)):
                    d[key] = doc_to_json_recursive(value)

            results.append(d)
        except Exception as e:
            logger.error(f"Error processing hackathon doc {doc.id}: {str(e)}")

    return results


@limits(calls=100, period=ONE_MINUTE)
def single_add_volunteer(event_id, json, volunteer_type, propel_id):
    db = _get_db()
    logger.info("Single Add Volunteer")
    logger.info("JSON: " + str(json))
    send_slack_audit(action="single_add_volunteer", message="Adding", payload=json)

    admin_email, admin_user_id, admin_last_login, admin_profile_image, admin_name, admin_nickname = get_propel_user_details_by_id(propel_id)

    if volunteer_type not in ["mentor", "volunteer", "judge"]:
        return Message(
            "Error: Must be volunteer, mentor, judge"
        )

    json["volunteer_type"] = volunteer_type
    json["event_id"] = event_id
    name = json["name"]

    json["created_by"] = admin_name
    json["created_timestamp"] = datetime.now().isoformat()

    logger.info(f"Checking to see if person {name} is already in DB for event: {event_id}")
    doc = db.collection('volunteers').where("event_id", "==", event_id).where("name", "==", name).stream()
    if len(list(doc)) > 0:
        logger.warning("Volunteer already exists")
        return Message("Volunteer already exists")

    logger.info(f"Checking to see if the event_id '{event_id}' provided exists")
    doc = db.collection('hackathons').where("event_id", "==", event_id).stream()
    if len(list(doc)) == 0:
        logger.warning("No hackathon found")
        return Message("No Hackathon Found")

    logger.info(f"Looks good! Adding to volunteers collection JSON: {json}")
    doc = db.collection('volunteers').add(json)

    get_volunteer_by_event.cache_clear()

    return Message(
        "Added Hackathon Volunteer"
    )


@limits(calls=50, period=ONE_MINUTE)
def update_hackathon_volunteers(event_id, volunteer_type, json, propel_id):
    db = _get_db()
    logger.info(f"update_hackathon_volunteers for event_id={event_id} propel_id={propel_id}")
    logger.info("JSON: " + str(json))
    send_slack_audit(action="update_hackathon_volunteers", message="Updating", payload=json)

    if "id" not in json:
        logger.error("Missing id field")
        return Message("Missing id field")

    volunteer_id = json["id"]

    admin_email, admin_user_id, admin_last_login, admin_profile_image, admin_name, admin_nickname = get_propel_user_details_by_id(propel_id)

    doc_ref = db.collection("volunteers").document(volunteer_id)
    doc = doc_ref.get()
    doc_dict = doc.to_dict()
    doc_volunteer_type = doc_dict.get("volunteer_type", "participant").lower()

    if doc_ref is None:
        return Message("No volunteer for Hackathon Found")

    json["updated_by"] = admin_name
    json["updated_timestamp"] = datetime.now().isoformat()

    doc_ref.update(json)

    slack_user_id = doc.get('slack_user_id')

    hackathon_welcome_message = f"🎉 Welcome <@{slack_user_id}> [{doc_volunteer_type}]."

    base_message = f"🎉 Welcome to Opportunity Hack {event_id}! You're checked in as a {doc_volunteer_type}.\n\n"

    if doc_volunteer_type == 'mentor':
        role_guidance = """🧠 As a Mentor:
• Help teams with technical challenges and project direction
• Share your expertise either by staying with a specific team, looking at GitHub to find a team that matches your skills, or asking for who might need a mentor in #ask-a-mentor
• Guide teams through problem-solving without doing the work for them
• Connect with teams in their Slack channels or in-person"""

    elif doc_volunteer_type == 'judge':
        role_guidance = """⚖️ As a Judge:
• Review team presentations and evaluate projects
• Focus on impact, technical implementation, and feasibility
• Provide constructive feedback during judging sessions
• Join the judges' briefing session for scoring criteria"""

    elif doc_volunteer_type == 'volunteer':
        role_guidance = """🙋 As a Volunteer:
• Help with event logistics and participant support
• Assist with check-in, meals, and general questions
• Support the organizing team throughout the event
• Be a friendly face for participants who need help"""

    else:  # hacker/participant
        role_guidance = """💻 As a Hacker:
• Form or join a team to work on nonprofit challenges
• Collaborate with your team to build meaningful tech solutions
• Attend mentor sessions and utilize available resources
• Prepare for final presentations and judging"""

    slack_message_content = f"""{base_message}{role_guidance}

📅 Important Links:
• Full schedule: https://www.ohack.dev/hack/{event_id}#countdown
• Slack channels: Watch #general for updates
• Need help? Ask in #help or find an organizer

🚀 Ready to code for good? Let's build technology that makes a real difference for nonprofits and people in the world!
"""

    if json.get("checkedIn") is True and "checkedIn" not in doc_dict.keys() and doc_dict.get("checkedIn") != True:
        logger.info(f"Volunteer {volunteer_id} just checked in, sending welcome message to {slack_user_id}")
        invite_user_to_channel(slack_user_id, "hackathon-welcome")

        logger.info(f"Sending Slack message to volunteer {volunteer_id} in channel #{slack_user_id} and #hackathon-welcome")
        async_send_slack(
            channel="#hackathon-welcome",
            message=hackathon_welcome_message
        )

        logger.info(f"Sending Slack DM to volunteer {volunteer_id} in channel #{slack_user_id}")
        async_send_slack(
            channel=slack_user_id,
            message=slack_message_content
        )
    else:
        logger.info(f"Volunteer {volunteer_id} checked in again, no welcome message sent.")

    get_volunteer_by_event.cache_clear()

    return Message(
        "Updated Hackathon Volunteers"
    )


from services.email_service import add_utm


def send_hackathon_request_email(contact_name, contact_email, request_id):
    """
    Send a specialized confirmation email to someone who has submitted a hackathon request.
    """
    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"hackathon_request_image_{image_number}"

    base_url = os.getenv("FRONTEND_URL", "https://www.ohack.dev")
    edit_link = f"{base_url}/hack/request/{request_id}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Thank You for Your Hackathon Request</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=image_utm_content)}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">

        <h1 style="color: #0088FE;">Thank You for Your Hackathon Request!</h1>

        <p>Dear {contact_name},</p>

        <p>We're thrilled you're interested in hosting an Opportunity Hack event! Your request has been received and our team is reviewing it now.</p>

        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h2 style="color: #0088FE; margin-top: 0;">Next Steps:</h2>
            <ol style="margin-bottom: 0;">
                <li>A member of our team will reach out within 3-5 business days</li>
                <li>We'll schedule a call to discuss your goals and requirements</li>
                <li>Together, we'll create a customized hackathon plan for your community</li>
            </ol>
        </div>

        <p><strong>Need to make changes to your request?</strong><br>
        You can <a href="{add_utm(edit_link, medium='email', campaign='hackathon_request', content=image_utm_content)}" style="color: #0088FE; font-weight: bold;">edit your request here</a> at any time.</p>

        <h2 style="color: #0088FE;">Why Host an Opportunity Hack?</h2>
        <ul>
            <li>Connect local nonprofits with skilled tech volunteers</li>
            <li>Build lasting technology solutions for social good</li>
            <li>Create meaningful community engagement opportunities</li>
            <li>Develop technical skills while making a difference</li>
        </ul>

        <p>Have questions in the meantime? Feel free to reply to this email or reach out through our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack community</a>.</p>

        <p>Together, we can create positive change through technology!</p>

        <p>Warm regards,<br>The Opportunity Hack Team</p>

        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """

    if contact_name is None or contact_name == "" or contact_name == "Unassigned" or contact_name.isspace():
        contact_name = "Event Organizer"

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{contact_name} <{contact_email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": "Your Opportunity Hack Event Request - Next Steps",
        "html": html_content,
    }

    try:
        email = resend.Emails.SendParams(params)
        resend.Emails.send(email)
        logger.info(f"Sent hackathon request confirmation email to {contact_email}")
        return True
    except Exception as e:
        logger.error(f"Error sending hackathon request email via Resend: {str(e)}")
        return False


def create_hackathon(json):
    db = _get_db()
    logger.debug("Hackathon Create")
    send_slack_audit(action="create_hackathon", message="Creating", payload=json)

    doc_id = uuid.uuid1().hex
    collection = db.collection('hackathon_requests')
    json["created"] = datetime.now().isoformat()
    json["status"] = "pending"
    insert_res = collection.document(doc_id).set(json)
    json["id"] = doc_id

    if "contactEmail" in json and "contactName" in json:
        send_hackathon_request_email(json["contactName"], json["contactEmail"], doc_id)

    send_slack(
        message=":rocket: New Hackathon Request :rocket: with json: " + str(json), channel="log-hackathon-requests", icon_emoji=":rocket:")
    logger.debug(f"Insert Result: {insert_res}")

    return {
        "message": "Hackathon Request Created",
        "success": True,
        "id": doc_id
    }


def get_hackathon_request_by_id(doc_id):
    db = _get_db()
    logger.debug("Hackathon Request Get")
    doc = db.collection('hackathon_requests').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="get_hackathon_request_by_id", message="Getting", payload=doc_dict)
        return doc_dict
    else:
        return None


def update_hackathon_request(doc_id, json):
    db = _get_db()
    logger.debug("Hackathon Request Update")
    doc = db.collection('hackathon_requests').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_hackathon_request", message="Updating", payload=doc_dict)
        send_hackathon_request_email(json["contactName"], json["contactEmail"], doc_id)
        doc_dict["updated"] = datetime.now().isoformat()

        doc.update(json)
        return doc_dict
    else:
        return None


def get_all_hackathon_requests():
    db = _get_db()
    logger.debug("Hackathon Requests List (Admin)")
    collection = db.collection('hackathon_requests')
    docs = collection.stream()
    requests = []
    for doc in docs:
        doc_dict = doc.to_dict()
        doc_dict["id"] = doc.id
        requests.append(doc_dict)
    requests.sort(key=lambda x: x.get("created", ""), reverse=True)
    return {"requests": requests}


def admin_update_hackathon_request(doc_id, json):
    db = _get_db()
    logger.debug("Hackathon Request Admin Update")
    doc_ref = db.collection('hackathon_requests').document(doc_id)
    doc_snapshot = doc_ref.get()
    if not doc_snapshot.exists:
        return None

    send_slack_audit(action="admin_update_hackathon_request", message="Admin updating", payload=json)
    json["updated"] = datetime.now().isoformat()
    doc_ref.update(json)

    updated_doc = doc_ref.get().to_dict()
    updated_doc["id"] = doc_id
    return updated_doc


@limits(calls=50, period=ONE_MINUTE)
def save_hackathon(json_data, propel_id):
    db = _get_db()
    logger.info("Hackathon Save/Update initiated")
    logger.debug(json_data)
    send_slack_audit(action="save_hackathon", message="Saving/Updating", payload=json_data)

    try:
        validate_hackathon_data(json_data)

        doc_id = json_data.get("id") or uuid.uuid1().hex
        is_update = "id" in json_data

        hackathon_data = {
            "title": json_data["title"],
            "description": json_data["description"],
            "location": json_data["location"],
            "start_date": json_data["start_date"],
            "end_date": json_data["end_date"],
            "type": json_data["type"],
            "image_url": json_data["image_url"],
            "event_id": json_data["event_id"],
            "links": json_data.get("links", []),
            "countdowns": json_data.get("countdowns", []),
            "constraints": json_data.get("constraints", {
                "max_people_per_team": 5,
                "max_teams_per_problem": 10,
                "min_people_per_team": 2,
            }),
            "donation_current": json_data.get("donation_current", {
                "food": "0",
                "prize": "0",
                "swag": "0",
                "thank_you": "",
            }),
            "donation_goals": json_data.get("donation_goals", {
                "food": "0",
                "prize": "0",
                "swag": "0",
            }),
            "timezone": json_data.get("timezone", "America/Phoenix"),
            "last_updated": firestore.SERVER_TIMESTAMP,
            "last_updated_by": propel_id,
        }

        if "nonprofits" in json_data:
            hackathon_data["nonprofits"] = [db.collection("nonprofits").document(npo) for npo in json_data["nonprofits"]]
        if "teams" in json_data:
            hackathon_data["teams"] = [db.collection("teams").document(team) for team in json_data["teams"]]

        @firestore.transactional
        def update_hackathon(transaction):
            hackathon_ref = db.collection('hackathons').document(doc_id)
            if is_update:
                transaction.set(hackathon_ref, hackathon_data, merge=True)
            else:
                hackathon_data["created_at"] = firestore.SERVER_TIMESTAMP
                hackathon_data["created_by"] = propel_id
                transaction.set(hackathon_ref, hackathon_data)

        transaction = db.transaction()
        update_hackathon(transaction)

        clear_cache()

        logger.info(f"Hackathon {'updated' if is_update else 'created'} successfully. ID: {doc_id}")
        return Message(
        "Saved Hackathon"
    )

    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        return {"error": str(ve)}, 400
    except Exception as e:
        logger.error(f"Error saving/updating hackathon: {str(e)}")
        return {"error": "An unexpected error occurred"}, 500
