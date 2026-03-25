from datetime import datetime
import os
import uuid
from ratelimit import limits
import requests
from db.db import delete_nonprofit, fetch_npo, fetch_npos, insert_nonprofit, update_nonprofit
from model.nonprofit import Nonprofit
import pytz
from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey
from firebase_admin import firestore
from common.log import get_logger, info, debug, warning, error, exception
from common.utils.slack import send_slack_audit, send_slack
from common.utils.validators import validate_email, validate_url
from common.exceptions import InvalidInputError
from common.utils.firestore_helpers import doc_to_json, doc_to_json_recursive
from api.messages.message import Message

logger = get_logger("nonprofits_service")

ONE_MINUTE = 1*60


def _get_db():
    from db.db import get_db
    return get_db()


def _clear_cache():
    from services.hackathons_service import clear_cache
    clear_cache()


# ==================== Model-based functions (existing) ====================

@limits(calls=20, period=ONE_MINUTE)
def get_npos():
    debug(logger, "Get NPOs start")

    npos = fetch_npos()

    debug(logger, "Found NPO results", count=len(npos))
    return npos

def get_npo(id):
    npo = fetch_npo(id)
    return npo

@limits(calls=50, period=ONE_MINUTE)
def save_npo(d):
    debug(logger, "Save NPO", nonprofit=d)

    n = Nonprofit()
    n.update(d)

    n = insert_nonprofit(n)

    return n

@limits(calls=50, period=ONE_MINUTE)
def update_npo(d):
    debug(logger, "Update NPO", nonprofit=d)

    n: Nonprofit | None = None

    if 'id' in d and d['id'] is not None:
        n = fetch_npo(d['id'])

    if n is not None:
        n.update(d)

        return update_nonprofit(n)

    else:
        return None

def delete_npo(id):
    n: Nonprofit | None = fetch_npo(id)

    if n is not None:
        n = delete_nonprofit(id)

    return n


# ==================== Raw-Firestore functions (from messages_service) ====================

@limits(calls=100, period=ONE_MINUTE)
def get_nonprofits_by_problem_statement_id(problem_statement_id):
    """Reverse lookup: given a problem statement ID, find the nonprofit(s) that own it."""
    logger.debug(f"get_nonprofits_by_problem_statement_id start ps_id={problem_statement_id}")
    db = _get_db()
    ps_ref = db.collection('problem_statements').document(problem_statement_id)

    try:
        docs = db.collection('nonprofits').where(
            'problem_statements', 'array_contains', ps_ref
        ).stream()

        results = []
        for doc in docs:
            npo = doc_to_json(docid=doc.id, doc=doc)
            if npo:
                results.append(npo)

        logger.info(f"get_nonprofits_by_problem_statement_id found {len(results)} nonprofits")
        return {"nonprofits": results}
    except Exception as e:
        logger.error(f"Error in get_nonprofits_by_problem_statement_id: {e}")
        return {"nonprofits": []}


@limits(calls=1000, period=ONE_MINUTE)
def get_single_npo(npo_id):
    logger.debug(f"get_npo start npo_id={npo_id}")
    db = _get_db()
    doc = db.collection('nonprofits').document(npo_id)

    if doc is None:
        logger.warning("get_npo end (no results)")
        return {}
    else:
        result = doc_to_json(docid=doc.id, doc=doc)

        logger.info(f"get_npo end (with result):{result}")
        return {
            "nonprofits": result
        }
    return {}


@limits(calls=40, period=ONE_MINUTE)
def get_npos_by_hackathon_id(id):
    logger.debug(f"get_npos_by_hackathon_id start id={id}")
    db = _get_db()
    doc = db.collection('hackathons').document(id)

    try:
        doc_dict = doc.get().to_dict()
        if doc_dict is None:
            logger.warning("get_npos_by_hackathon_id end (no results)")
            return {
                "nonprofits": []
            }

        npos = []
        if "nonprofits" in doc_dict and doc_dict["nonprofits"]:
            npo_refs = doc_dict["nonprofits"]
            logger.info(f"get_npos_by_hackathon_id found {len(npo_refs)} nonprofit references")

            for npo_ref in npo_refs:
                try:
                    npo_doc = npo_ref.get()
                    if npo_doc.exists:
                        npo = doc_to_json(docid=npo_doc.id, doc=npo_doc)
                        npos.append(npo)
                except Exception as e:
                    logger.error(f"Error processing nonprofit reference: {e}")
                    continue

        return {
            "nonprofits": npos
        }
    except Exception as e:
        logger.error(f"Error in get_npos_by_hackathon_id: {e}")
        return {
            "nonprofits": []
        }


@limits(calls=40, period=ONE_MINUTE)
def get_npo_by_hackathon_id(id):
    logger.debug(f"get_npo_by_hackathon_id start id={id}")
    db = _get_db()
    doc = db.collection('hackathons').document(id)

    if doc is None:
        logger.warning("get_npo_by_hackathon_id end (no results)")
        return {}
    else:
        result = doc_to_json(docid=doc.id, doc=doc)

        logger.info(f"get_npo_by_hackathon_id end (with result):{result}")
        return result
    return {}


@limits(calls=20, period=ONE_MINUTE)
def get_npo_list(word_length=30):
    logger.debug("NPO List Start")
    db = _get_db()
    docs = db.collection('nonprofits').order_by( "rank" ).stream()
    if docs is None:
        return {[]}
    else:
        results = []
        for doc in docs:
            logger.debug(f"Processing doc {doc.id} {doc}")
            results.append(doc_to_json_recursive(doc=doc))

    logger.debug(f"Found {len(results)} results {results}")
    return { "nonprofits": results }


@limits(calls=100, period=ONE_MINUTE)
def save_npo_legacy(json):
    """Legacy raw-Firestore save_npo from messages_service."""
    send_slack_audit(action="save_npo", message="Saving", payload=json)
    db = _get_db()
    logger.info("NPO Save - Starting")

    try:
        required_fields = ['name', 'description', 'website', 'slack_channel']
        for field in required_fields:
            if field not in json or not json[field].strip():
                raise InvalidInputError(f"Missing or empty required field: {field}")

        name = json['name'].strip()
        description = json['description'].strip()
        website = json['website'].strip()
        slack_channel = json['slack_channel'].strip()
        contact_people = json.get('contact_people', [])
        contact_email = json.get('contact_email', [])
        problem_statements = json.get('problem_statements', [])
        image = json.get('image', '').strip()
        rank = int(json.get('rank', 0))

        contact_email = [email.strip() for email in contact_email if validate_email(email.strip())]

        if not validate_url(website):
            raise InvalidInputError("Invalid website URL")

        problem_statement_refs = [
            db.collection("problem_statements").document(ps)
            for ps in problem_statements
            if ps.strip()
        ]

        npo_data = {
            "name": name,
            "description": description,
            "website": website,
            "slack_channel": slack_channel,
            "contact_people": contact_people,
            "contact_email": contact_email,
            "problem_statements": problem_statement_refs,
            "image": image,
            "rank": rank,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP
        }

        @firestore.transactional
        def save_npo_transaction(transaction):
            existing_npo = db.collection('nonprofits').where("name", "==", name).limit(1).get()
            if len(existing_npo) > 0:
                raise InvalidInputError(f"Nonprofit with name '{name}' already exists")

            new_doc_ref = db.collection('nonprofits').document()

            transaction.set(new_doc_ref, npo_data)

            return new_doc_ref

        transaction = db.transaction()
        new_npo_ref = save_npo_transaction(transaction)

        logger.info(f"NPO Save - Successfully saved nonprofit: {new_npo_ref.id}")
        send_slack_audit(action="save_npo", message="Saved successfully", payload={"id": new_npo_ref.id})

        _clear_cache()

        return Message(f"Saved NPO with ID: {new_npo_ref.id}")

    except InvalidInputError as e:
        logger.error(f"NPO Save - Invalid input: {str(e)}")
        return Message(f"Failed to save NPO: {str(e)}", status="error")
    except Exception as e:
        logger.exception("NPO Save - Unexpected error occurred")
        return Message("An unexpected error occurred while saving the NPO", status="error")


@limits(calls=100, period=ONE_MINUTE)
def remove_npo_legacy(json):
    """Legacy raw-Firestore remove_npo from messages_service."""
    logger.debug("Start NPO Delete")
    doc_id = json["id"]
    db = _get_db()
    doc = db.collection('nonprofits').document(doc_id)
    if doc:
        send_slack_audit(action="remove_npo", message="Removing", payload=doc.get().to_dict())
        doc.delete()

    logger.debug("End NPO Delete")
    return Message(
        "Delete NPO"
    )


@limits(calls=20, period=ONE_MINUTE)
def update_npo_legacy(json):
    """Legacy raw-Firestore update_npo from messages_service."""
    db = _get_db()

    logger.debug("NPO Edit")
    send_slack_audit(action="update_npo", message="Updating", payload=json)

    doc_id = json["id"]
    temp_problem_statements = json["problem_statements"]

    doc = db.collection('nonprofits').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_npo", message="Updating", payload=doc_dict)

        name = json.get("name", None)
        contact_email = json.get("contact_email", None)
        contact_people = json.get("contact_people", None)
        slack_channel = json.get("slack_channel", None)
        website = json.get("website", None)
        description = json.get("description", None)
        image = json.get("image", None)
        rank = json.get("rank", None)

        if isinstance(contact_email, str):
            contact_email = [email.strip() for email in contact_email.split(',')]
        if isinstance(contact_people, str):
            contact_people = [person.strip() for person in contact_people.split(',')]

        problem_statements = []
        for ps in temp_problem_statements:
            problem_statements.append(db.collection("problem_statements").document(ps))

        update_data = {
            "contact_email": contact_email,
            "contact_people": contact_people,
            "name": name,
            "slack_channel": slack_channel,
            "website": website,
            "description": description,
            "problem_statements": problem_statements,
            "image": image,
            "rank": rank
        }

        update_data = {k: v for k, v in update_data.items() if v is not None}
        logger.debug(f"Update data: {update_data}")

        doc.update(update_data)

        logger.debug("NPO Edit - Update successful")
        send_slack_audit(action="update_npo", message="Update successful", payload=update_data)

        _clear_cache()

        return Message("Updated NPO")
    else:
        logger.error(f"NPO Edit - Document with id {doc_id} not found")
        return Message("NPO not found", status="error")


@limits(calls=100, period=ONE_MINUTE)
def update_npo_application(application_id, json, propel_id):
    send_slack_audit(action="update_npo_application", message="Updating", payload=json)
    db = _get_db()
    logger.info("NPO Application Update")
    doc = db.collection('project_applications').document(application_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_npo_application",
                         message="Updating", payload=doc_dict)
        doc.update(json)

    logger.info(f"Clearing cache for application_id={application_id}")

    _clear_cache()

    return Message(
        "Updated NPO Application"
    )


@limits(calls=100, period=ONE_MINUTE)
def get_npo_applications():
    logger.info("get_npo_applications Start")
    db = _get_db()

    @firestore.transactional
    def get_latest_docs(transaction):
        docs = db.collection('project_applications').get(transaction=transaction)
        return [doc_to_json(docid=doc.id, doc=doc) for doc in docs]

    transaction = db.transaction()
    results = get_latest_docs(transaction)

    if not results:
        return {"applications": []}

    logger.info(results)
    logger.info("get_npo_applications End")

    return {"applications": results}


@limits(calls=100, period=ONE_MINUTE)
def save_npo_application(json):
    from services.email_service import send_nonprofit_welcome_email, google_recaptcha_key

    send_slack_audit(action="save_npo_application", message="Saving", payload=json)
    db = _get_db()
    logger.debug("NPO Application Save")

    token = json["token"]
    recaptcha_response = requests.post(
        f"https://www.google.com/recaptcha/api/siteverify?secret={google_recaptcha_key}&response={token}")
    recaptcha_response_json = recaptcha_response.json()
    logger.info(f"Recaptcha Response: {recaptcha_response_json}")

    if recaptcha_response_json["success"] == False:
        return Message(
            "Recaptcha failed"
        )

    doc_id = uuid.uuid1().hex

    name = json["name"]
    email = json["email"]
    organization = json["organization"]
    idea = json["idea"]
    isNonProfit = json["isNonProfit"]

    collection = db.collection('project_applications')

    insert_res = collection.document(doc_id).set({
        "name": name,
        "email": email,
        "organization": organization,
        "idea": idea,
        "isNonProfit": isNonProfit,
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Insert Result: {insert_res}")

    logger.info(f"Sending welcome email to {name} {email}")

    send_nonprofit_welcome_email(organization, name, email)

    logger.info(f"Sending slack message to nonprofit-form-submissions")

    slack_message = f'''
:rocket: New NPO Application :rocket:
Name: `{name}`
Email: `{email}`
Organization: `{organization}`
Idea: `{idea}`
Is Nonprofit: `{isNonProfit}`
'''
    send_slack(channel="nonprofit-form-submissions", message=slack_message, icon_emoji=":rocket:")

    logger.info(f"Sent slack message to nonprofit-form-submissions")

    return Message(
        "Saved NPO Application"
    )
