import uuid
from datetime import datetime

from firebase_admin import firestore

from common.log import get_logger
from common.utils.slack import send_slack_audit
from db.db import get_db
from services.users_service import get_slack_user_from_propel_user_id, get_user_from_slack_id
from api.messages.message import Message

logger = get_logger("giveaway_service")


def save_giveaway(propel_user_id, json):
    db = get_db()
    logger.info("Submitting Giveaway")
    send_slack_audit(action="submit_giveaway", message="Submitting", payload=json)

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    user_db_id = get_user_from_slack_id(slack_user["sub"]).id
    giveaway_id = json.get("giveaway_id")
    giveaway_data = json.get("giveaway_data", {})
    entries = json.get("entries", 0)

    collection = db.collection('giveaways')

    doc_id = uuid.uuid1().hex
    insert_res = collection.document(doc_id).set({
        "user_id": user_db_id,
        "giveaway_id": giveaway_id,
        "entries": entries,
        "giveaway_data": giveaway_data,
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Insert Result: {insert_res}")

    return Message("Giveaway submitted successfully")


def get_user_giveaway(propel_user_id):
    logger.info(f"Getting giveaway for propel_user_id: {propel_user_id}")
    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    db_user_id = get_user_from_slack_id(slack_user["sub"]).id

    giveaway_docs = db.collection('giveaways').where("user_id", "==", db_user_id).stream()

    giveaway_list = []
    for doc in giveaway_docs:
        giveaway = doc.to_dict()
        giveaway_list.append(giveaway)

    return {"giveaways": giveaway_list}


def get_all_giveaways():
    logger.info("Getting all giveaways")
    db = get_db()
    docs = db.collection('giveaways').order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    giveaways = {}
    for doc in docs:
        giveaway = doc.to_dict()
        user_id = giveaway["user_id"]
        if user_id not in giveaways:
            from api.messages.messages_service import get_user_by_id_old
            user = get_user_by_id_old(user_id)
            giveaway["user"] = user
            giveaways[user_id] = giveaway

    return {"giveaways": list(giveaways.values())}
