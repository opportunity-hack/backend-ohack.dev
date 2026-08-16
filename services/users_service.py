from datetime import datetime
import os
import threading
from ratelimit import limits
import requests
from common.utils.slack import send_slack_audit, get_slack_user_by_email
from model.user import User, internal_lookup_fields
from db.db import delete_user_by_db_id, delete_user_by_user_id, fetch_user_by_user_id, fetch_user_by_db_id, fetch_user_by_propel_id, fetch_user_by_email, fetch_users, insert_user, update_user, get_user_profile_by_db_id, upsert_profile_metadata, fetch_user_by_github
import pytz
from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey
from common.utils.redis_cache import redis_cached
from common.utils.validators import sanitize_string, validate_url
from common.log import get_logger, info, debug, warning, error, exception
import uuid


logger = get_logger("users_service")

# Import OAuth utilities for handling multiple providers (Slack, Google, etc.)
from common.utils.oauth_providers import (
    USER_ID_PREFIX,
    normalize_slack_user_id,
    is_oauth_user_id,
    get_oauth_provider_from_propel_response,
    build_user_id_for_provider,
    extract_slack_user_id,
)

#TODO consts file?
ONE_MINUTE = 1*60

# USER_ID_PREFIX is now imported from oauth_providers module for consistency
# Note: This maintains backward compatibility with Slack-specific code

def clear_cache():
    get_profile_metadata.cache_clear()
    clear_portfolio_caches()


def clear_portfolio_caches(db_id=None):
    """Invalidate the public-portfolio caches after any profile-affecting write.

    Redis prefixes are cleared wholesale (clear_pattern) — entries are few and
    per-key deletion would need the exact hashed cache_key.
    """
    from common.utils.redis_cache import clear_pattern
    for prefix in ("portfolio:profile", "portfolio:resolve", "portfolio:teams", "portfolio:sitemap"):
        try:
            clear_pattern(f"{prefix}:*")
        except Exception as e:
            warning(logger, "Failed to clear portfolio cache", prefix=prefix, error=str(e))

    # The profile EDITOR reads through the legacy /api/messages/profile path,
    # which has its own TTL caches — slug/visibility/bio-video writes must
    # flush those too or the editor shows stale data for up to 10 minutes.
    try:
        from api.messages import messages_service
        messages_service.get_profile_metadata_old.cache_clear()
        messages_service.get_user_by_id_old.cache_clear()
    except Exception as e:
        warning(logger, "Failed to clear legacy profile caches", error=str(e))

def finish_saving_insert(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None,
        propel_id=None):
    user = User()
    user.user_id = user_id
    user.email_address = email
    user.last_login = last_login
    user.profile_image = profile_image
    user.name = name
    user.nickname = nickname
    user.propel_id = propel_id
    return insert_user(user)

def finish_saving_update(
        user,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None,
        propel_id=None
        ):
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        user.propel_id = propel_id
        return update_user(user)


@limits(calls=50, period=ONE_MINUTE)
def save_user(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None,
        propel_id=None,
        ):

    info(logger, "User Save", user_id=user_id, email=email, last_login=last_login, profile_image=profile_image, name=name, nickname=nickname)
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc

    if user_id is None or email is None or last_login is None or profile_image is None:
        error(logger, "Empty values provided for user save", 
              user_id=user_id, email=email, 
              last_login=last_login, profile_image=profile_image)
        return None

    # TODO: Call get_user from db here
    user = fetch_user_by_user_id(user_id)

    if user is not None:
        user = finish_saving_update(user, last_login, profile_image, name, nickname, propel_id)
        
    else:
        user = finish_saving_insert(user_id, email, last_login, profile_image, name, nickname,propel_id)

    return user if user is not None else None

def get_slack_user_from_token(token):
    resp = requests.get(
        "https://slack.com/api/openid.connect.userInfo",
        headers={"Authorization": f"Bearer {token}"}
    )
    '''
    {'ok': True, 'sub': 'UC31XTRT5', 'https://slack.com/user_id': 'UC31XTRT5', 'https://slack.com/team_id': 'T1Q7936BH', 'email': 'greg.vannoni@gmail.com', 'email_verified': True, 'date_email_verified': 1632009763, 'name': 'Greg V [Staff/Mentor]', 'picture': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_512.png', 'given_name': 'Greg', 'family_name': 'V [Staff/Mentor]', 'locale': 'en-US', 'https://slack.com/team_name': 'Opportunity Hack', 'https://slack.com/team_domain': 'opportunity-hack', 'https://slack.com/user_image_24': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_24.png', 'https://slack.com/user_image_32': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_32.png', 'https://slack.com/user_image_48': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_48.png', 'https://slack.com/user_image_72': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_72.png', 'https://slack.com/user_image_192': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_192.png', 'https://slack.com/user_image_512': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_512.png', 'https://slack.com/user_image_1024': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_1024.png', 'https://slack.com/team_image_34': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_34.png', 'https://slack.com/team_image_44': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_44.png', 'https://slack.com/team_image_68': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_68.png', 'https://slack.com/team_image_88': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_88.png', 'https://slack.com/team_image_102': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_102.png', 'https://slack.com/team_image_132': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_132.png',
    'https://slack.com/team_image_230': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_230.png', 'https://slack.com/team_image_default': False}
    '''

    json = resp.json()
    if not json["ok"]:
        warning(logger, "Error getting user details from Slack or Propel APIs", response=json)
        return None

    if "sub" not in json:
        warning(logger, "Error getting user details from Slack or Propel APIs", response=json)
        return None

    # Add prefix to sub
    json["sub"] = USER_ID_PREFIX + json["sub"]

    info(logger, "Slack API response", response=json)
    return json


def get_google_user_from_token(token):
    """
    Get Google user details from a Google OAuth access token.

    Google userinfo response format:
    {
        'sub': '1234567890',  # Google's unique user ID
        'email': 'user@gmail.com',
        'email_verified': True,
        'name': 'John Doe',
        'given_name': 'John',
        'family_name': 'Doe',
        'picture': 'https://lh3.googleusercontent.com/...',
        'locale': 'en'
    }
    """
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {token}"}
    )

    json = resp.json()

    if "error" in json:
        warning(logger, "Error getting user details from Google API", response=json)
        return None

    if "sub" not in json:
        warning(logger, "Missing 'sub' field in Google API response", response=json)
        return None

    # Build the normalized user ID for Google
    json["sub"] = build_user_id_for_provider("google", json["sub"])

    info(logger, "Google API response", response=json)
    return json

def get_user_from_propel_user_id(propel_id):
    oauth_user = get_oauth_user_from_propel_user_id(propel_id)
    if oauth_user is None:
        return None
    user_id = oauth_user["sub"]
    return get_user_from_slack_id(user_id)


# Two-tier PropelAuth cache: positive results (10 min) + negative sentinel (5 min).
# Intentionally in-process only — the payload contains OAuth access tokens.
_PROPEL_CACHE = TTLCache(maxsize=512, ttl=600)
_PROPEL_MISS_CACHE = TTLCache(maxsize=512, ttl=300)
_PROPEL_LOCK = threading.Lock()
_PROPEL_MISS = object()  # sentinel so we can cache None explicitly


def get_oauth_user_from_propel_user_id(propel_id):
    """
    Get user details from any OAuth provider via PropelAuth.

    Cached in-process (10 min hit / 5 min miss) — do NOT put in Redis since
    the response contains OAuth access tokens.
    """
    with _PROPEL_LOCK:
        if propel_id in _PROPEL_MISS_CACHE:
            # Don't fail silently: a tight retry window otherwise shows only the
            # caller's "could not resolve" warnings with no root cause, because
            # the original failure reason was logged >5 min ago (or evicted).
            debug(logger, "Serving cached OAuth miss (root cause logged earlier this TTL window)", propel_id=propel_id)
            return None
        if propel_id in _PROPEL_CACHE:
            return _PROPEL_CACHE[propel_id]

    url = f"{os.getenv('PROPEL_AUTH_URL')}/api/backend/v1/user/{propel_id}/oauth_token"

    debug(logger, "Propel API URL", url=url)

    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {os.getenv('PROPEL_AUTH_KEY')}"}
    )
    logger.debug(f"Propel RESP: {resp}")
    if resp.status_code != 200:
        # Log the response body (truncated) — the status alone doesn't tell us
        # WHY (e.g. user has no linked OAuth connection, token revoked, wrong
        # PROPEL_AUTH_URL/KEY env). This is the gap that hid the root cause.
        body = ""
        try:
            body = resp.text[:300]
        except Exception:
            pass
        warning(logger, "PropelAuth oauth_token API returned non-200",
                status=resp.status_code, propel_id=propel_id, body=body)
        with _PROPEL_LOCK:
            _PROPEL_MISS_CACHE[propel_id] = _PROPEL_MISS
        return None
    json_resp = resp.json()
    logger.debug(f"Propel RESP JSON: {json_resp}")

    # Detect which OAuth provider was used
    provider, token_data = get_oauth_provider_from_propel_response(json_resp)

    if provider is None or token_data is None:
        warning(logger, "Could not detect OAuth provider from PropelAuth response", response=json_resp)
        with _PROPEL_LOCK:
            _PROPEL_MISS_CACHE[propel_id] = _PROPEL_MISS
        return None

    access_token = token_data.get('access_token')
    if not access_token:
        warning(logger, "No access token found in PropelAuth response", provider=provider, response=json_resp)
        with _PROPEL_LOCK:
            _PROPEL_MISS_CACHE[propel_id] = _PROPEL_MISS
        return None

    debug(logger, "Detected OAuth provider", provider=provider)

    # Fetch user details from the appropriate provider
    if provider == 'slack':
        result = get_slack_user_from_token(access_token)
    elif provider == 'google':
        result = get_google_user_from_token(access_token)
    else:
        warning(logger, "Unsupported OAuth provider", provider=provider)
        result = None

    with _PROPEL_LOCK:
        if result is not None:
            _PROPEL_CACHE[propel_id] = result
        else:
            _PROPEL_MISS_CACHE[propel_id] = _PROPEL_MISS
    return result


def get_slack_user_from_propel_user_id(propel_id):
    """
    Legacy function for backward compatibility.
    Use get_oauth_user_from_propel_user_id instead.
    """
    return get_oauth_user_from_propel_user_id(propel_id)

def get_propel_user_details_by_id(propel_id):
    """
    Get normalized user details from PropelAuth for any OAuth provider.

    Returns:
        tuple: (email, user_id, last_login, profile_image, name, nickname)
    """
    oauth_user = get_oauth_user_from_propel_user_id(propel_id)

    if oauth_user is None:
        warning(logger, "Could not get OAuth user details", propel_id=propel_id)
        return None, None, None, None, None, None

    user_id = oauth_user["sub"]
    email = oauth_user.get("email", "")

    # Use today's date in UTC (Z)
    last_login = datetime.now().isoformat() + "Z"
    logger.debug(f"Last Login: {last_login} {datetime.now().astimezone(pytz.timezone('US/Arizona')).isoformat()}")

    # Get profile image - handle different provider formats
    # Slack uses: https://slack.com/user_image_192
    # Google uses: picture
    profile_image = (
        oauth_user.get("https://slack.com/user_image_192") or
        oauth_user.get("picture") or
        ""
    )

    # Get name - both providers use 'name'
    name = oauth_user.get("name", "")

    # Get nickname - both providers use 'given_name'
    nickname = oauth_user.get("given_name", "")

    return email, user_id, last_login, profile_image, name, nickname

def get_profile_by_db_id(id):
    # Log
    logger.debug(f"Get User By ID: {id}")
    u = get_user_profile_by_db_id(id)

    res = None

    # internal_lookup_fields = safe_public_fields + github. github is included
    # here (unlike the fully public/privacy-filtered portfolio) because this
    # route backs internal features — team rosters, peer feedback, admin
    # giveaways — that have always shown a participant's GitHub username.
    # propel_id is PII and everything else beyond this list is privacy-gated;
    # those need get_privacy_filtered_profile_by_db_id instead.
    fields = list(internal_lookup_fields)

    if u is not None:
        # Check if the field is in the response first
        temp = vars(u)
        res = {k: temp[k] for k in fields if k in temp}

    
    logger.debug(f"Get User By ID Result: {res}")
    return res    
    
def _refresh_login_details(user, propel_id):
    """Best-effort login refresh (last_login + provider avatar/name).

    The 3-tier resolver's fast path (stored propel_id) makes no external call
    and therefore doesn't refresh these like save_user used to. The OAuth
    round-trip here is strictly optional — when it's down we still stamp
    last_login and move on. Never raises.
    """
    payload = {"last_login": datetime.now().isoformat() + "Z"}
    try:
        _email, user_id, _last_login, profile_image, name, nickname = \
            get_propel_user_details_by_id(propel_id)
        if user_id:
            payload.update({
                "profile_image": profile_image,
                "name": name,
                "nickname": nickname,
            })
    except Exception as e:
        warning(logger, "Login-detail refresh skipped (provider unavailable)",
                propel_id=propel_id, error=str(e))
    try:
        from db.db import update_user_login
        update_user_login(user.id, payload)
    except Exception as e:
        warning(logger, "Failed to write login refresh", propel_id=propel_id, error=str(e))


# 10 minute cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock())
@limits(calls=100, period=ONE_MINUTE)
def get_profile_metadata(propel_id):
    """Own-profile read. Identity via the 3-tier resolver — a broken OAuth
    provider token can no longer 404 the profile page (the old path depended
    solely on the live OAuth round-trip)."""
    logger.debug("Profile Metadata")

    user, user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        warning(logger, "Could not resolve user for profile read", propel_id=propel_id)
        return None

    send_slack_audit(
        action="login", message=f"User went to profile: {user_id} with email: {user.email_address}")

    _refresh_login_details(user, propel_id)

    # Re-read through the profile loader (resolves badge refs) and serialize
    full_user = get_history(user.id)
    if full_user is None:
        return None
    response = build_profile_response(full_user)
    logger.debug(f"get_profile_metadata {response}")

    return response

# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_history(db_id):
    logger.debug("Get History Start")
    result = get_user_profile_by_db_id(db_id)

    logger.debug(f"RESULT\n{result}")
    return result


def build_profile_response(user):
    """THE canonical own-profile response dict. Both /api/users/profile and
    the legacy /api/messages/profile delegates serve exactly this (the legacy
    route wraps it in its historical {"text": ...} envelope).

    hackathons/hackathon_history are attendance-derived from the volunteers
    collection (the documented source of truth) — NOT the deprecated
    users.hackathons ref array.
    """
    d = user.serialize_profile_fields()
    try:
        from services.volunteers_service import get_user_hackathon_attendance
        hackathons = get_user_hackathon_attendance(
            user_id=getattr(user, "user_id", None),
            email=getattr(user, "email_address", None),
        )
    except Exception as e:
        warning(logger, "Failed to load hackathon attendance for profile response",
                db_id=getattr(user, "id", None), error=str(e))
        hackathons = []
    d["hackathons"] = hackathons
    d["hackathon_history"] = hackathons
    return d

MAX_BIO_LENGTH = 2000
MAX_HEADLINE_LENGTH = 80
MAX_PORTFOLIO_LINKS = 10
MAX_LINK_LABEL_LENGTH = 40
MAX_LINK_URL_LENGTH = 300


def _sanitize_portfolio_metadata(metadata):
    """Sanitize the portfolio-specific metadata fields in place.

    bio/headline are length-capped; portfolio_links is rebuilt as a clean
    [{label, url}] array (invalid URLs dropped, https:// auto-prefixed).
    """
    if "bio" in metadata:
        metadata["bio"] = sanitize_string(metadata.get("bio") or "", MAX_BIO_LENGTH)
    if "headline" in metadata:
        metadata["headline"] = sanitize_string(metadata.get("headline") or "", MAX_HEADLINE_LENGTH)
    if "portfolio_links" in metadata:
        raw = metadata.get("portfolio_links")
        links = []
        if isinstance(raw, list):
            for item in raw[:MAX_PORTFOLIO_LINKS]:
                if not isinstance(item, dict):
                    continue
                url = (item.get("url") or "").strip()
                if not url or any(c.isspace() for c in url):
                    continue  # validate_url's urlparse check lets spaces through
                if not url.lower().startswith(("http://", "https://")):
                    url = f"https://{url}"
                if not validate_url(url):
                    continue
                links.append({
                    "label": sanitize_string(item.get("label") or "", MAX_LINK_LABEL_LENGTH),
                    "url": url[:MAX_LINK_URL_LENGTH],
                })
        metadata["portfolio_links"] = links
    return metadata


def save_profile_metadata(propel_id, json):
    """Own-profile write. Identity via the 3-tier resolver (lazily creates the
    doc for brand-new users) — no longer blocked by a broken OAuth token."""

    send_slack_audit(action="save_profile_metadata", message="Saving", payload=json)

    if not json or "metadata" not in json:
        warning(logger, "save_profile_metadata called without metadata", propel_id=propel_id)
        return None

    user, user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        warning(logger, "Could not resolve user for profile save", propel_id=propel_id)
        return None

    logger.info(f"Save Profile Metadata for {user_id} {json}")

    json = json["metadata"]

    _sanitize_portfolio_metadata(json)
    user.update_from_metadata(json)
    upsert_profile_metadata(user)

    # Clear cache for get_profile_metadata
    get_profile_metadata.cache_clear()
    clear_portfolio_caches(user.id)

    return build_profile_response(user)

def get_user_by_db_id(id):
    user = fetch_user_by_db_id(id)
    if user is not None:
        return user
    # Accept vanity slugs anywhere a db id is accepted (public routes)
    resolved = resolve_user_db_id(id)
    if resolved and resolved != id:
        return fetch_user_by_db_id(resolved)
    return None

def get_slack_user_id_by_github(github_username):
    """Look up a Slack user ID given a GitHub username."""
    if not github_username:
        return None
    user = fetch_user_by_github(github_username)
    if user is None:
        return None
    raw_id = extract_slack_user_id(user.user_id)
    import re
    if re.match(r'^[UW][A-Z0-9]{5,}$', raw_id):
        return raw_id
    if user.email_address:
        slack_user = get_slack_user_by_email(user.email_address)
        if slack_user:
            return slack_user.get('id')
    return None

def get_user_from_slack_id(user_id):
    return fetch_user_by_user_id(user_id)

def remove_user_by_db_id(id):
    return delete_user_by_db_id(id)

def remove_user_by_slack_id(user_id):
    return delete_user_by_user_id(user_id)

@limits(calls=100, period=ONE_MINUTE)
def get_users():
    return fetch_users()

def _fetch_propel_metadata(propel_id):
    """Fetch PropelAuth user metadata (email/name/avatar) for a propel_id.

    This hits PropelAuth's user-metadata API, which is RELIABLE and does NOT
    depend on the OAuth provider (Slack/Google) token — unlike
    get_oauth_user_from_propel_user_id, which calls the provider's userinfo
    endpoint and fails when the provider token is missing/expired. Returns a
    plain dict {email, name, nickname, profile_image} or None.
    """
    try:
        from common.auth import auth  # lazy import: keeps this module cheap to import/test
        meta = auth.fetch_user_metadata_by_user_id(propel_id)
    except Exception as e:  # network / SDK / not-found
        warning(logger, "PropelAuth fetch_user_metadata_by_user_id failed", propel_id=propel_id, error=str(e))
        return None
    if meta is None:
        warning(logger, "PropelAuth returned no metadata for user", propel_id=propel_id)
        return None
    email = getattr(meta, "email", None) or ""
    first = getattr(meta, "first_name", None) or ""
    last = getattr(meta, "last_name", None) or ""
    name = (f"{first} {last}".strip()) or getattr(meta, "username", None) or email
    return {
        "email": email,
        "name": name,
        "nickname": first,
        "profile_image": getattr(meta, "picture_url", None) or "",
    }


def _resolve_and_ensure_user(propel_id):
    """Resolve a propel_id to a Firestore User, lazily creating the doc for
    brand-new users. Returns (user, user_id); (None, None) only when NO identity
    source can resolve the user.

    Resolution order — most-reliable first, so a broken OAuth provider token can
    never block volunteering:
      1. **Direct lookup by the stored `propel_id` field** — no external call.
         Covers everyone who has saved a profile (propel_id is set then).
      2. **OAuth provider round-trip** (`get_oauth_user_from_propel_user_id` ->
         provider `sub` -> user_id lookup) — yields the OAuth-format user_id +
         provider avatar; lazily creates a doc for a brand-new user.
      3. **PropelAuth user-metadata fallback** (`fetch_user_metadata_by_user_id`)
         — reliable, does NOT depend on the provider token. Resolves an existing
         doc by email (backfilling propel_id), else lazily creates one from the
         metadata. This is what saves the user when #1 misses AND the OAuth
         round-trip is down.

    The bug history: the WRITE used to depend SOLELY on #2. When the provider
    round-trip returned None (expired/unavailable token, PropelAuth hiccup, or
    its 5-min negative cache), the write 404'd ("Couldn't log that time") while
    the read masked it by returning empty data. #1 + #3 remove that dependency.
    Once a doc is created/backfilled with propel_id, every later request hits #1.
    """
    # 1) Fast, reliable path: existing doc carries propel_id.
    user = fetch_user_by_propel_id(propel_id)
    if user is not None:
        return user, (user.user_id or propel_id)

    # 2) OAuth provider round-trip — best source (OAuth-format user_id + avatar).
    oauth_user = get_oauth_user_from_propel_user_id(propel_id)
    if oauth_user is not None:
        user_id = oauth_user["sub"]
        user = fetch_user_by_user_id(user_id)
        if user is None:
            info(logger, "Lazily creating user doc on first volunteering action", user_id=user_id)
            save_user(
                user_id=user_id,
                email=oauth_user.get("email", ""),
                last_login=datetime.now().isoformat() + "Z",
                profile_image=(
                    oauth_user.get("https://slack.com/user_image_192")
                    or oauth_user.get("picture")
                    or ""
                ),
                name=oauth_user.get("name", ""),
                nickname=oauth_user.get("given_name", ""),
                propel_id=propel_id,
            )
            user = fetch_user_by_user_id(user_id)
        return user, user_id

    # 3) OAuth is down — fall back to PropelAuth metadata (no provider token).
    info(logger, "OAuth round-trip unavailable; resolving via PropelAuth metadata", propel_id=propel_id)
    meta = _fetch_propel_metadata(propel_id)
    if meta is None or not meta.get("email"):
        warning(logger, "Could not resolve user by propel_id, OAuth, or metadata", propel_id=propel_id)
        return None, None

    email = meta["email"]
    user = fetch_user_by_email(email)
    if user is not None:
        # Backfill propel_id so every future request hits the fast path (#1).
        if getattr(user, "propel_id", None) != propel_id:
            user.propel_id = propel_id
            try:
                upsert_profile_metadata(user)
            except Exception as e:
                warning(logger, "Failed to backfill propel_id on user", propel_id=propel_id, error=str(e))
        return user, (user.user_id or propel_id)

    # Brand-new user, OAuth down: create a doc from metadata. user_id is set to
    # the propel UUID (we lack the oauth-format id without the provider call);
    # propel_id is the canonical match, so #1 resolves this user from now on.
    info(logger, "Lazily creating user doc from PropelAuth metadata (OAuth unavailable)", propel_id=propel_id)
    save_user(
        user_id=propel_id,
        email=email,
        last_login=datetime.now().isoformat() + "Z",
        profile_image=meta.get("profile_image", ""),
        name=meta.get("name", ""),
        nickname=meta.get("nickname", ""),
        propel_id=propel_id,
    )
    user = fetch_user_by_propel_id(propel_id) or fetch_user_by_email(email)
    if user is None:
        warning(logger, "Metadata-based user creation did not yield a doc", propel_id=propel_id)
        return None, None
    return user, (user.user_id or propel_id)


def save_volunteering_time(propel_id, json):
    logger.info(f"Save Volunteering Time for {propel_id} {json}")
    user, user_id = _resolve_and_ensure_user(propel_id)
    if user is None:
        warning(logger, "Could not resolve/create user for volunteering save", propel_id=propel_id)
        return None

    # Allow backdating a manually-logged entry; default to now (UTC).
    timestamp = json.get("timestamp") or (datetime.now().isoformat() + "Z")
    reason = json.get("reason", "")  # The kind of volunteering being done

    # A single entry can carry committed hours (set when a live session starts),
    # actively-tracked hours (set when a session ends), or BOTH (manual log of
    # actual time done away from the keyboard). Counting both on one entry is fine
    # because get_volunteering_time no longer concatenates two filtered lists.
    def _clean_hours(value):
        try:
            hours = round(float(value), 2)
        except (TypeError, ValueError):
            return None
        if hours < 0:
            return None
        return min(hours, 1000)  # defensive cap against garbage input

    commitment_hours = _clean_hours(json["commitmentHours"]) if "commitmentHours" in json else None
    final_hours = _clean_hours(json["finalHours"]) if "finalHours" in json else None

    if commitment_hours is None and final_hours is None:
        error(logger, "No valid hours provided for volunteering entry", user_id=user_id)
        return None

    entry = {"timestamp": timestamp, "reason": reason}
    if commitment_hours is not None:
        entry["commitmentHours"] = commitment_hours
    if final_hours is not None:
        entry["finalHours"] = final_hours
    if json.get("manual"):
        entry["manual"] = True

    user.volunteering.append(entry)
    # Targeted write — volunteering is NOT in the generic profile write set
    # (a concurrent profile save must never clobber a volunteering log).
    from db.db import update_user_volunteering
    update_user_volunteering(user)

    # Clear cache for get_profile_metadata
    get_profile_metadata.cache_clear()

    return user

def get_volunteering_time(propel_id, start_date, end_date):
    logger.info(f"Get Volunteering Time for {propel_id} {start_date} {end_date}")
    user, user_id = _resolve_and_ensure_user(propel_id)
    if user is None:
        # Identity couldn't be resolved (transient OAuth issue). Return an empty
        # dataset rather than 404 so the page shows a clean zero-state.
        warning(logger, "Could not resolve user for volunteering read; returning empty", propel_id=propel_id)
        return [], 0, 0

    def _in_range(v):
        if start_date is None or end_date is None:
            return True
        ts = v.get("timestamp", "")
        return start_date <= ts <= end_date

    # Single pass, no duplication. Each entry appears once and contributes to
    # whichever totals its fields cover.
    filtered = [v for v in (user.volunteering or []) if _in_range(v)]
    total_active_hours = round(sum((v.get("finalHours") or 0) for v in filtered), 2)
    total_commitment_hours = round(sum((v.get("commitmentHours") or 0) for v in filtered), 2)

    logger.debug(
        f"volunteering entries: {len(filtered)} "
        f"Total Active Hours: {total_active_hours} "
        f"Total Commitment Hours: {total_commitment_hours}"
    )

    return filtered, total_active_hours, total_commitment_hours

def get_all_volunteering_time(start_date=None, end_date=None):
    logger.info(f"Get All Volunteering Time for start: {start_date} end: {end_date}")

    # Get all users
    users = fetch_users()

    all_volunteering = []
    total_active_hours = 0
    total_commitment_hours = 0

    for user in users:
        # Filter the volunteering data
        for v in user.volunteering:
            # Create a copy of the volunteering record to add user information
            session_copy = v.copy() if isinstance(v, dict) else dict(v)
            
            # Add user information to each volunteering record
            session_copy["userName"] = user.name if hasattr(user, 'name') else "Unknown User"
            session_copy["userId"] = user.id 
            session_copy["email"] = user.email_address if hasattr(user, 'email_address') else "N/A"
            
            # Track hours based on session type
            if "finalHours" in session_copy:
                if start_date is not None and end_date is not None:
                    if session_copy["timestamp"] >= start_date and session_copy["timestamp"] <= end_date:
                        total_active_hours += float(session_copy["finalHours"])
                        all_volunteering.append(session_copy)
                else:
                    total_active_hours += float(session_copy["finalHours"])
                    all_volunteering.append(session_copy)
            
            elif "commitmentHours" in session_copy:
                if start_date is not None and end_date is not None:
                    if session_copy["timestamp"] >= start_date and session_copy["timestamp"] <= end_date:
                        total_commitment_hours += float(session_copy["commitmentHours"])
                        all_volunteering.append(session_copy)
                else:
                    total_commitment_hours += float(session_copy["commitmentHours"])
                    all_volunteering.append(session_copy)

    # Process the results to ensure consistent structure
    processed_volunteering = []
    for session in all_volunteering:
        # Make sure the session has all required fields with proper types
        processed_session = {
            **session,
            "timestamp": session.get("timestamp", datetime.now().isoformat()),
            "commitmentHours": float(session.get("commitmentHours", 0)),
            "finalHours": float(session.get("finalHours", 0)),
            "userName": session.get("userName", "Unknown User"),
            "userId": session.get("userId", f"unknown-{uuid.uuid4()}"),
            "email": session.get("email", "N/A"),
            "reason": session.get("reason", "")
        }
        processed_volunteering.append(processed_session)
    
    return processed_volunteering, total_active_hours, total_commitment_hours


def get_privacy_settings(propel_id):
    """Get privacy settings for a user"""
    logger.info(f"Get Privacy Settings for {propel_id}")
    oauth_user = get_oauth_user_from_propel_user_id(propel_id)
    if oauth_user is None:
        warning(logger, "Could not get OAuth user from PropelAuth", propel_id=propel_id)
        return None

    user_id = oauth_user["sub"]

    # Get the user
    user = fetch_user_by_user_id(user_id)
    if user is None:
        warning(logger, "User not found", user_id=user_id)
        return None

    return user.get_privacy_settings()


def update_privacy_settings(propel_id, data):
    """Update privacy settings for a user"""
    logger.info(f"Update Privacy Settings for {propel_id} {data}")
    oauth_user = get_oauth_user_from_propel_user_id(propel_id)
    if oauth_user is None:
        warning(logger, "Could not get OAuth user from PropelAuth", propel_id=propel_id)
        return None

    user_id = oauth_user["sub"]

    # Get the user
    user = fetch_user_by_user_id(user_id)
    if user is None:
        warning(logger, "User not found", user_id=user_id)
        return None

    # Update the privacy settings
    updated_settings = {}
    for field, is_public in data.items():
        if user.update_privacy_setting(field, is_public):
            updated_settings[field] = is_public
        else:
            warning(logger, f"Invalid privacy field: {field}")

    # Save to database
    upsert_profile_metadata(user)

    # Clear cache for get_profile_metadata
    get_profile_metadata.cache_clear()
    clear_portfolio_caches(user.id)

    return user.get_privacy_settings()


PUBLIC_PRAISES_PREVIEW_LIMIT = 3


def resolve_user_db_id(id_or_slug):
    """Resolve a /profile URL param (db id OR vanity slug) to a db id.

    Direct doc ids always win (legacy links); the slug pointer collection is
    only consulted when no user doc has that id. Returns None when neither
    resolves.
    """
    if not id_or_slug:
        return None
    from db.db import fetch_user_db_id_by_slug
    pointer = fetch_user_db_id_by_slug(str(id_or_slug).strip().lower())
    if pointer and pointer.get("user_db_id"):
        return pointer["user_db_id"]
    return None


def _get_user_profile_by_db_id_or_slug(id_or_slug):
    """get_user_profile_by_db_id that transparently accepts a vanity slug."""
    user = get_user_profile_by_db_id(id_or_slug)
    if user is not None:
        return user
    resolved = resolve_user_db_id(id_or_slug)
    if resolved and resolved != id_or_slug:
        return get_user_profile_by_db_id(resolved)
    return None


def _attach_hackathon_history(user, public_data, privacy_settings):
    """Replace the legacy hackathons array with attendance derived from volunteers."""
    if privacy_settings.get("hackathon_history") != "public":
        return
    try:
        from services.volunteers_service import get_user_hackathon_attendance
        history = get_user_hackathon_attendance(
            user_id=getattr(user, 'user_id', None),
            email=getattr(user, 'email_address', None),
        )
    except Exception as e:
        warning(logger, "Failed to load hackathon attendance for public profile",
                user_id=getattr(user, 'user_id', None), exc_info=e)
        return

    public_data["hackathon_history"] = history
    # Keep the legacy key populated so older frontends don't break during rollout
    public_data["hackathons"] = history


def _trim_event_for_portfolio(event):
    if not event:
        return None
    keep = ("event_id", "title", "start_date", "end_date", "location", "image_url")
    return {k: event.get(k) for k in keep if event.get(k) is not None}


@redis_cached(prefix="portfolio:teams", ttl=900)
def _fetch_portfolio_teams_cached(db_id):
    """User's teams (allowlisted) with a trimmed `event` object attached."""
    from db.db import fetch_user_portfolio_teams
    from common.utils.firebase import get_hackathon_by_event_id

    teams = fetch_user_portfolio_teams(db_id)
    if not teams:
        return []

    events = {}
    for event_id in {t.get("hackathon_event_id") for t in teams if t.get("hackathon_event_id")}:
        try:
            events[event_id] = _trim_event_for_portfolio(get_hackathon_by_event_id(event_id))
        except Exception as e:
            warning(logger, "Failed to enrich portfolio team event", event_id=event_id, error=str(e))

    for team in teams:
        team["event"] = events.get(team.get("hackathon_event_id"))

    # Newest event first; teams with no event date sink to the end
    teams.sort(key=lambda t: ((t.get("event") or {}).get("start_date") or ""), reverse=True)
    return teams


def _attach_teams(user, public_data, privacy_settings):
    """Attach hackathon teams (demo videos, repos, awards) when opted in."""
    if privacy_settings.get("teams") != "public":
        return
    try:
        public_data["teams"] = _fetch_portfolio_teams_cached(user.id)
    except Exception as e:
        warning(logger, "Failed to load teams for public profile",
                db_id=getattr(user, 'id', None), exc_info=e)


def _attach_certificates(user, public_data, privacy_settings):
    """Attach heart certificates (from history) + git-fame GitHub certificates."""
    if privacy_settings.get("certificates") != "public":
        return

    cdn_server = os.getenv("CDN_SERVER", "https://cdn.ohack.dev")
    certificates = {"heart_certificates": [], "github_certificates": []}

    history = getattr(user, "history", {}) or {}
    for entry in (history.get("certificates") or []):
        if isinstance(entry, str):
            certificates["heart_certificates"].append({"url": f"{cdn_server}/certificates/{entry}"})
        elif isinstance(entry, dict):
            url = entry.get("url")
            if not url and entry.get("filename"):
                url = f"{cdn_server}/certificates/{entry['filename']}"
            if not url:
                continue
            certificates["heart_certificates"].append({
                "url": url,
                "timestamp": entry.get("timestamp"),
                "reasons": entry.get("reasons"),
                "hearts": entry.get("hearts"),
            })

    github = getattr(user, "github", "") or ""
    if github:
        try:
            from api.certificates.certificate_service import get_certificates_by_github_username
            cert_allow = ("certificate_url", "date", "repository_url", "stats", "file_id")
            for cert in (get_certificates_by_github_username(github) or []):
                # Allowlist — author_email must never leak to the public payload
                certificates["github_certificates"].append(
                    {k: cert.get(k) for k in cert_allow if cert.get(k) is not None}
                )
        except Exception as e:
            warning(logger, "Failed to load GitHub certificates for public profile",
                    github=github, exc_info=e)

    if certificates["heart_certificates"] or certificates["github_certificates"]:
        public_data["certificates"] = certificates


def _attach_github_contributions(user, public_data, privacy_settings):
    """Attach stored GitHub contribution history when opted in."""
    if privacy_settings.get("github_history") != "public":
        return
    github = getattr(user, "github", "") or ""
    if not github:
        return
    try:
        from common.utils.firebase import get_github_contributions_for_user
        public_data["github_history"] = get_github_contributions_for_user(github)
    except Exception as e:
        warning(logger, "Failed to load GitHub contributions for public profile",
                github=github, exc_info=e)


def _attach_hearts(user, public_data, privacy_settings):
    """Attach the hearts total + tier summary when opted in. Zero extra reads."""
    if privacy_settings.get("hearts") != "public":
        return
    try:
        from services.hearts_service import get_hearts_summary
        public_data["hearts"] = get_hearts_summary(getattr(user, "history", {}) or {})
    except Exception as e:
        warning(logger, "Failed to compute hearts summary for public profile",
                db_id=getattr(user, 'id', None), exc_info=e)


def _attach_received_praises(user, public_data, privacy_settings):
    """Attach praises_count + praises_recent when the user opts in."""
    if privacy_settings.get("praises") != "public":
        return
    user_id = getattr(user, 'user_id', None)
    if not user_id:
        return
    # Praise records key off the raw Slack user_id (e.g. 'U12345ABC'); our
    # User.user_id is stored prefixed (e.g. 'oauth2|slack|T1Q7936BH-U12345ABC'),
    # so strip the prefix before querying.
    slack_user_id = extract_slack_user_id(user_id)
    try:
        from services.news_service import get_praises_about_user
        message = get_praises_about_user(slack_user_id)
        praises = message.text if hasattr(message, 'text') else []
        if praises is None:
            praises = []
    except Exception as e:
        warning(logger, "Failed to load praises for public profile",
                user_id=user_id, exc_info=e)
        return

    public_data["praises_count"] = len(praises)
    public_data["praises_recent"] = praises[:PUBLIC_PRAISES_PREVIEW_LIMIT]


def get_privacy_filtered_profile_by_db_id(db_id):
    """Get privacy-filtered profile data by database ID or vanity slug"""
    logger.debug(f"Get Privacy-Filtered Profile By DB ID: {db_id}")
    user = _get_user_profile_by_db_id_or_slug(db_id)

    if user is None:
        logger.debug("User not found")
        return None

    # Get privacy-filtered public data
    public_data = user.get_public_profile_data()
    privacy_settings = user.get_privacy_settings()

    _attach_hackathon_history(user, public_data, privacy_settings)
    _attach_received_praises(user, public_data, privacy_settings)
    _attach_teams(user, public_data, privacy_settings)
    _attach_certificates(user, public_data, privacy_settings)
    _attach_github_contributions(user, public_data, privacy_settings)
    _attach_hearts(user, public_data, privacy_settings)

    logger.debug(f"Privacy-Filtered Profile Result: {public_data}")
    return public_data


# Bio video: either uploaded to our CDN (signed-URL direct upload) or a link
# to an allowlisted video provider (rendered via the frontend's VideoDisplay).
ALLOWED_VIDEO_CONTENT_TYPES = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
}
MAX_BIO_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB
ALLOWED_VIDEO_LINK_HOSTS = {
    "youtube.com", "www.youtube.com", "youtu.be",
    "vimeo.com", "player.vimeo.com", "www.vimeo.com",
    "loom.com", "www.loom.com",
}


def _cdn_server():
    return os.getenv("CDN_SERVER", "https://cdn.ohack.dev").rstrip("/")


def create_bio_video_upload_url(propel_id, content_type, content_length):
    """Mint a signed GCS PUT URL for a bio video. Returns (payload, status)."""
    if content_type not in ALLOWED_VIDEO_CONTENT_TYPES:
        return {"error": f"content_type must be one of {sorted(ALLOWED_VIDEO_CONTENT_TYPES)}"}, 400
    try:
        content_length = int(content_length)
    except (TypeError, ValueError):
        return {"error": "content_length is required"}, 400
    if content_length <= 0 or content_length > MAX_BIO_VIDEO_BYTES:
        return {"error": f"Video must be under {MAX_BIO_VIDEO_BYTES // (1024 * 1024)}MB"}, 400

    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"error": "Could not resolve your account"}, 404

    from common.utils.cdn import generate_signed_upload_url
    ext = ALLOWED_VIDEO_CONTENT_TYPES[content_type]
    filename = f"bio_video_{uuid.uuid4().hex}.{ext}"
    try:
        payload = generate_signed_upload_url(
            directory=f"users/{user.id}",
            filename=filename,
            content_type=content_type,
            max_bytes=MAX_BIO_VIDEO_BYTES,
        )
    except Exception as e:
        exception(logger, "Failed to generate signed upload URL", error=str(e))
        return {"error": "Could not create an upload URL"}, 500
    return payload, 200


def set_bio_video_url(propel_id, url):
    """The single writer for bio_video_url. Returns (payload, status).

    Accepts: null/"" (clear), an own-CDN URL under users/<db_id>/ (verified to
    exist), or an allowlisted provider link (YouTube/Vimeo/Loom).
    bio_video_url is deliberately NOT in metadata_list — arbitrary URLs must
    never reach the public page.
    """
    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"error": "Could not resolve your account"}, 404

    url = (url or "").strip()
    previous = getattr(user, "bio_video_url", "") or ""
    cdn_prefix = f"{_cdn_server()}/users/{user.id}/"

    if url:
        if url.startswith(cdn_prefix):
            blob_path = url[len(_cdn_server()) + 1:]
            try:
                from common.utils.cdn import get_blob_metadata
                meta = get_blob_metadata(blob_path)
            except Exception as e:
                exception(logger, "Failed to verify uploaded bio video", error=str(e))
                return {"error": "Could not verify the uploaded video"}, 500
            if not meta.get("exists"):
                return {"error": "Upload not found — did the upload finish?"}, 400
            if meta.get("content_type") not in ALLOWED_VIDEO_CONTENT_TYPES:
                return {"error": "Uploaded file is not an allowed video type"}, 400
            if (meta.get("size") or 0) > MAX_BIO_VIDEO_BYTES:
                return {"error": "Uploaded video exceeds the size limit"}, 400
        else:
            if not validate_url(url):
                return {"error": "Invalid URL"}, 400
            from urllib.parse import urlparse
            host = (urlparse(url).netloc or "").lower().split(":")[0]
            if host not in ALLOWED_VIDEO_LINK_HOSTS:
                return {"error": "Video links must be YouTube, Vimeo, or Loom (or an upload)"}, 400

    from db.db import update_user_bio_video
    update_user_bio_video(user.id, url)

    # Best-effort cleanup of a replaced/removed own-CDN upload
    if previous and previous.startswith(cdn_prefix) and previous != url:
        try:
            from common.utils.cdn import delete_from_cdn
            delete_from_cdn(previous[len(_cdn_server()) + 1:])
        except Exception as e:
            warning(logger, "Failed to delete previous bio video", error=str(e))

    send_slack_audit(action="set_bio_video_url", message=f"User {user.id} set bio video")
    get_profile_metadata.cache_clear()
    clear_portfolio_caches(user.id)
    return {"bio_video_url": url}, 200


def set_profile_visibility(propel_id, visibility):
    """Set the portfolio master toggle. Returns (payload, http_status).

    "public" (search-indexable + sitemap-listed) requires a claimed slug so
    every indexed portfolio has a clean /u/<slug> URL.
    """
    from model.user import PROFILE_VISIBILITY_VALUES

    if visibility not in PROFILE_VISIBILITY_VALUES:
        return {"error": f"visibility must be one of {list(PROFILE_VISIBILITY_VALUES)}"}, 400

    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"error": "Could not resolve your account"}, 404

    if visibility == "public" and not getattr(user, "profile_slug", None):
        return {"error": "Claim a portfolio URL before making your portfolio public"}, 400

    from db.db import update_user_profile_visibility
    update_user_profile_visibility(user.id, visibility)

    send_slack_audit(action="set_profile_visibility",
                     message=f"User {user.id} set portfolio visibility to {visibility}")
    get_profile_metadata.cache_clear()
    clear_portfolio_caches(user.id)
    return {"profile_visibility": visibility}, 200


@redis_cached(prefix="portfolio:sitemap", ttl=3600)
def get_searchable_portfolio_sitemap():
    """[{slug, last_login}] for every opted-in public portfolio (sitemap feed)."""
    from db.db import fetch_public_portfolio_users
    return fetch_public_portfolio_users()


@redis_cached(prefix="portfolio:profile", ttl=300)
def get_portfolio_profile(id_or_slug):
    """Cached public portfolio payload (the fat response the profile page SSRs).

    Cache is keyed on the requested param (db id, slug, or alias each get their
    own 300s entry); clear_portfolio_caches() wipes the whole prefix on any
    profile-affecting write. Misses (None) are not cached by redis_cached.
    """
    return get_privacy_filtered_profile_by_db_id(id_or_slug)


def get_received_praises_by_db_id(db_id, limit=20, offset=0):
    """Return praises received by the user identified by `db_id`, honoring privacy.

    Returns a dict ``{"praises": [...], "total": int}`` or None if the user is
    not found / has praises set to private.
    """
    logger.debug(f"Get Received Praises By DB ID: {db_id} limit={limit} offset={offset}")
    user = _get_user_profile_by_db_id_or_slug(db_id)
    if user is None:
        return None

    privacy_settings = user.get_privacy_settings()
    if privacy_settings.get("praises") != "public":
        return None

    user_id = getattr(user, 'user_id', None)
    if not user_id:
        return {"praises": [], "total": 0}
    slack_user_id = extract_slack_user_id(user_id)

    try:
        from services.news_service import get_praises_about_user
        message = get_praises_about_user(slack_user_id)
        praises = message.text if hasattr(message, 'text') else []
        if praises is None:
            praises = []
    except Exception as e:
        warning(logger, "Failed to load praises", user_id=user_id, exc_info=e)
        return {"praises": [], "total": 0}

    total = len(praises)
    safe_limit = max(1, min(int(limit) if limit else 20, 50))
    safe_offset = max(0, int(offset) if offset else 0)
    return {
        "praises": praises[safe_offset: safe_offset + safe_limit],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


def get_public_privacy_settings_by_db_id(db_id):
    """Get only the privacy settings for a user by database ID (for public profile views)"""
    logger.debug(f"Get Public Privacy Settings By DB ID: {db_id}")
    user = _get_user_profile_by_db_id_or_slug(db_id)

    if user is None:
        logger.debug("User not found")
        return None

    # Return only the privacy settings - no user data
    privacy_settings = user.get_privacy_settings()
    logger.debug(f"Public Privacy Settings Result: {privacy_settings}")
    return privacy_settings
