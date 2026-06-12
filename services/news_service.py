import os
import threading
import time
from datetime import datetime

import pytz
from cachetools import cached, TTLCache
from ratelimit import limits

from common.log import get_logger
from common.utils.firebase import (
    upsert_news,
    upsert_praise,
    get_user_by_user_id,
    get_recent_praises,
    get_praises_by_user_id,
    update_news_partial,
    delete_news as delete_news_doc,
    get_all_news_admin,
)
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
    try:
        news_image = generate_and_save_image_to_cdn(cdn_dir, json["title"])
        json["image"] = f"{CDN_SERVER}/{cdn_dir}/{news_image}"
    except Exception as e:
        logger.exception(f"Image generation failed for title '{json['title']}': {e}")
        json["image"] = None
    json["last_updated"] = datetime.now().isoformat()
    news_id = upsert_news(json)

    logger.info("Updated news successfully")

    get_news.cache_clear()
    logger.info("Cleared cache for get_news")

    msg = Message("Saved News")
    msg.id = news_id
    return msg


_ADMIN_ALLOWED_KEYS = {
    "title",
    "description",
    "content_markdown",
    "content_format",
    "image",
    "featured_image",
    "author",
    "tags",
    "slug",
    "status",
    "published_at",
    "seo",
    "links",
    "slack_permalink",
    "slack_channel",
    "last_updated_by",
}


def _filter_admin_payload(json_in):
    return {k: v for k, v in (json_in or {}).items() if k in _ADMIN_ALLOWED_KEYS}


def admin_create_news(json_in, actor):
    """Create a news doc via the admin UI.

    Differences vs. save_news (the Slack-integration path):
      - Does NOT auto-generate an OpenAI image when featured_image is supplied
      - Defaults status="draft" so nothing surprises the public
      - Stamps slack_ts to time.time() if missing so existing ordering works
      - Records last_updated_by from actor
    """
    payload = _filter_admin_payload(json_in)

    if not payload.get("title"):
        return Message("Missing title"), 400

    payload.setdefault("description", "")
    payload.setdefault("content_format", "markdown")
    payload.setdefault("status", "draft")
    payload.setdefault("tags", [])
    payload.setdefault("links", [])
    payload.setdefault("slack_permalink", "")
    payload.setdefault("slack_channel", "admin-blog")
    payload["slack_ts"] = str(time.time())

    featured = payload.get("featured_image") or payload.get("image")
    if featured:
        payload["image"] = featured
        payload["featured_image"] = featured
    else:
        cdn_dir = "ohack.dev/news"
        try:
            news_image = generate_and_save_image_to_cdn(cdn_dir, payload["title"])
            payload["image"] = f"{CDN_SERVER}/{cdn_dir}/{news_image}"
        except Exception as e:
            logger.warning(f"admin_create_news: image generation failed, continuing without ({e})")

    payload["last_updated"] = datetime.now().isoformat()
    if actor:
        payload["last_updated_by"] = actor
        payload.setdefault("created_by", actor)

    news_id = upsert_news(payload)
    get_news.cache_clear()

    msg = Message("Created news")
    msg.id = news_id
    return msg, 201


def admin_update_news(news_id, json_in, actor):
    """Partial update of a news doc."""
    patch = _filter_admin_payload(json_in)
    if not patch:
        return Message("No allowed fields in payload"), 400

    if "featured_image" in patch and patch["featured_image"]:
        patch["image"] = patch["featured_image"]

    if actor:
        patch["last_updated_by"] = actor

    ok = update_news_partial(news_id, patch)
    if not ok:
        return Message("Not found"), 404
    get_news.cache_clear()
    msg = Message("Updated news")
    msg.id = news_id
    return msg, 200


def admin_delete_news(news_id):
    """Hard delete a news doc."""
    ok = delete_news_doc(news_id)
    if not ok:
        return Message("Not found"), 404
    get_news.cache_clear()
    return Message("Deleted news"), 200


def admin_list_news(limit=500, status_filter=None):
    """Admin list — includes drafts/archived posts."""
    results = get_all_news_admin(limit=limit, status_filter=status_filter)
    return Message(results)


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


@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock())
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


@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock())
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


def _is_publicly_visible(doc_dict):
    """A post is public unless explicitly marked as draft or archived."""
    status = (doc_dict or {}).get("status", "published")
    return status not in ("draft", "archived")


@cached(cache=TTLCache(maxsize=100, ttl=32600), lock=threading.Lock(), key=lambda news_limit, news_id: f"{news_limit}-{news_id}")
def get_news(news_limit=3, news_id=None):
    logger.debug("Get News")
    db = get_db()
    if news_id is not None:
        logger.info(f"Getting single news item for news_id={news_id}")
        collection = db.collection('news')
        doc = collection.document(news_id).get()
        if doc is None or not doc.exists:
            return Message({})
        doc_dict = doc.to_dict()
        if not _is_publicly_visible(doc_dict):
            # Public route: don't leak drafts / archived posts
            return Message({})
        return Message(doc_dict)
    else:
        # Over-fetch a bit so status filtering doesn't return fewer than asked
        fetch_limit = max(news_limit * 3, news_limit + 10)
        collection = db.collection('news')
        docs = collection.order_by("slack_ts", direction=firestore.Query.DESCENDING).limit(fetch_limit).stream()
        results = []
        for doc in docs:
            doc_json = doc.to_dict()
            if not _is_publicly_visible(doc_json):
                continue
            doc_json["id"] = doc.id
            results.append(doc_json)
            if len(results) >= news_limit:
                break
        logger.debug(f"Get News Result: {len(results)} items")
        return Message(results)
