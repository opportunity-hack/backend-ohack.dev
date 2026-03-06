import os
from datetime import datetime

import pytz
from cachetools import cached, TTLCache
from ratelimit import limits

from common.log import get_logger
from common.utils.firebase import upsert_news, upsert_praise, get_user_by_user_id, get_recent_praises, get_praises_by_user_id
from common.utils.openai_api import generate_and_save_image_to_cdn
from common.utils.slack import get_user_info
from common.utils.firestore_helpers import doc_to_json
from db.db import get_db
from api.messages.message import Message
from firebase_admin import firestore

logger = get_logger("news_service")

CDN_SERVER = os.getenv("CDN_SERVER")
ONE_MINUTE = 60


def save_news(json):
    check_fields = ["title", "description", "slack_ts", "slack_permalink", "slack_channel"]
    for field in check_fields:
        if field not in json:
            logger.error(f"Missing field {field} in {json}")
            return Message("Missing field")

    cdn_dir = "ohack.dev/news"
    news_image = generate_and_save_image_to_cdn(cdn_dir, json["title"])
    json["image"] = f"{CDN_SERVER}/{cdn_dir}/{news_image}"
    json["last_updated"] = datetime.now().isoformat()
    upsert_news(json)

    logger.info("Updated news successfully")

    get_news.cache_clear()
    logger.info("Cleared cache for get_news")

    return Message("Saved News")


def save_praise(json):
    check_fields = ["praise_receiver", "praise_channel", "praise_message"]
    for field in check_fields:
        if field not in json:
            logger.error(f"Missing field {field} in {json}")
            return Message("Missing field")

    logger.debug(f"Detected required fields, attempting to save praise")
    json["timestamp"] = datetime.now(pytz.utc).astimezone().isoformat()

    try:
        receiver_user = get_user_by_user_id(json["praise_receiver"])
        if receiver_user and "id" in receiver_user:
            json["praise_receiver_ohack_id"] = receiver_user["id"]
            logger.debug(f"Added praise_receiver_ohack_id: {receiver_user['id']}")
        else:
            logger.warning(f"Could not find ohack.dev user for praise_receiver: {json['praise_receiver']}")
            json["praise_receiver_ohack_id"] = None

        sender_user = get_user_by_user_id(json["praise_sender"])
        if sender_user and "id" in sender_user:
            json["praise_sender_ohack_id"] = sender_user["id"]
            logger.debug(f"Added praise_sender_ohack_id: {sender_user['id']}")
        else:
            logger.warning(f"Could not find ohack.dev user for praise_sender: {json['praise_sender']}")
            json["praise_sender_ohack_id"] = None

    except Exception as e:
        logger.error(f"Error getting ohack.dev user IDs: {str(e)}")
        json["praise_receiver_ohack_id"] = None
        json["praise_sender_ohack_id"] = None

    logger.info(f"Attempting to save the praise with the json object {json}")
    upsert_praise(json)

    logger.info("Updated praise successfully")

    get_praises_about_user.cache_clear()
    logger.info("Cleared cache for get_praises_by_user_id")

    get_all_praises.cache_clear()
    logger.info("Cleared cache for get_all_praises")

    return Message("Saved praise")


@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_all_praises():
    results = get_recent_praises()

    slack_ids = set()
    for r in results:
        slack_ids.add(r["praise_receiver"])
        slack_ids.add(r["praise_sender"])

    logger.info(f"SlackIDS: {slack_ids}")
    slack_user_info = get_user_info(slack_ids)
    logger.info(f"Slack User Info; {slack_user_info}")

    for r in results:
        r['praise_receiver_details'] = slack_user_info[r['praise_receiver']]
        r['praise_sender_details'] = slack_user_info[r['praise_sender']]

    logger.info(f"Here are the 20 most recently written praises: {results}")
    return Message(results)


@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_praises_about_user(user_id):
    results = get_praises_by_user_id(user_id)

    slack_ids = set()
    for r in results:
        slack_ids.add(r["praise_receiver"])
        slack_ids.add(r["praise_sender"])
    logger.info(f"Slack IDs: {slack_ids}")
    slack_user_info = get_user_info(slack_ids)
    logger.info(f"Slack User Info: {slack_user_info}")
    for r in results:
        r['praise_receiver_details'] = slack_user_info[r['praise_receiver']]
        r['praise_sender_details'] = slack_user_info[r['praise_sender']]

    logger.info(f"Here are all praises related to {user_id}: {results}")
    return Message(results)


@cached(cache=TTLCache(maxsize=100, ttl=32600), key=lambda news_limit, news_id: f"{news_limit}-{news_id}")
def get_news(news_limit=3, news_id=None):
    logger.debug("Get News")
    db = get_db()
    if news_id is not None:
        logger.info(f"Getting single news item for news_id={news_id}")
        collection = db.collection('news')
        doc = collection.document(news_id).get()
        if doc is None:
            return Message({})
        else:
            return Message(doc.to_dict())
    else:
        collection = db.collection('news')
        docs = collection.order_by("slack_ts", direction=firestore.Query.DESCENDING).limit(news_limit).stream()
        results = []
        for doc in docs:
            doc_json = doc.to_dict()
            doc_json["id"] = doc.id
            results.append(doc_json)
        logger.debug(f"Get News Result: {results}")
        return Message(results)
