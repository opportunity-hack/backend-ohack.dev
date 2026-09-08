from datetime import datetime
import threading
from ratelimit import limits
from common.utils.slack import invite_user_to_channel, send_slack, send_slack_audit
from common.utils.oauth_providers import extract_slack_user_id, is_slack_user_id
from model.problem_statement import ProblemStatement
from model.user import User
from db.db import (fetch_hackathon, fetch_problem_statements, get_db,
                  delete_problem_statement, fetch_problem_statement,
                  insert_problem_statement, update_problem_statement,
                  insert_problem_statement_hackathon, update_problem_statement_hackathons)
import logging
from cachetools import cached, TTLCache
from cachetools.keys import hashkey
import uuid
from services import users_service
from common.log import get_logger, info, debug, warning, error, exception
from common.exceptions import InvalidInputError

logger = get_logger("problem_statements_service")

ONE_MINUTE = 60
CACHE_TTL = 600  # 10 minutes

_ps_list_cache: TTLCache = TTLCache(maxsize=1, ttl=CACHE_TTL)

@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement(d):
    """
    Create or update a problem statement.
    Raises InvalidInputError if validation fails.
    """
    try:
        validate_problem_statement(d)
        
        p = ProblemStatement()
        p.update(d)

        if p.id is None:
            p = insert_problem_statement(p)
        else:
            p = update_problem_statement(p)

        # Clear relevant caches
        get_problem_statement.cache_clear()
        _ps_list_cache.clear()

        send_slack_audit(action="save_problem_statement",
                        message="Saving", payload=d)

        return p

    except Exception as e:
        exception(logger, "Error saving problem statement", exc_info=e)
        raise

def validate_problem_statement(data):
    """Validate problem statement data"""
    required_fields = ['title', 'description']
    
    for field in required_fields:
        if field not in data or not data[field].strip():
            raise InvalidInputError(f"Missing or empty required field: {field}")

    # Add any additional validation logic here
    return True

@cached(cache=TTLCache(maxsize=100, ttl=CACHE_TTL), lock=threading.Lock())
def get_problem_statement(id):
    """Get a single problem statement by ID"""
    debug(logger, "get_problem_statement start", id=id)    
    
    problem_statement = fetch_problem_statement(id)
    
    if problem_statement is None:
        warning(logger, "get_problem_statement end (no results)", id=id)
    else:                                
        info(logger, "get_problem_statement end (with result)", id=id, problem_statement=problem_statement)
        
    return problem_statement

def remove_problem_statement(id):
    """Delete a problem statement"""
    try:
        result = delete_problem_statement(id)
        
        # Clear caches
        get_problem_statement.cache_clear()
        _ps_list_cache.clear()
        
        return result
    except Exception as e:
        exception(logger, "Error deleting problem statement", exc_info=e, id=id)
        raise

@cached(cache=_ps_list_cache)
def get_problem_statements():
    """Get all problem statements"""
    return fetch_problem_statements()

@limits(calls=50, period=ONE_MINUTE)
def update_problem_statement_fields(d):
    
    problem_statement = None
    if 'id' in d and d['id'] is not None:
        problem_statement = fetch_problem_statement(d['id'])
    
    if problem_statement is not None:
        problem_statement.update(d)
        problem_statement.id = d['id']
        result = update_problem_statement(problem_statement)
        
        # Clear cache after updating problem statement
        get_problem_statement.cache_clear()
        
        return result
    else:
        return None
    
@limits(calls=100, period=ONE_MINUTE)
def save_helping_status(propel_user_id, d):
    """Toggle the caller's "helping" status on a problem statement.

    Port of the legacy messages_service.save_helping_status_old body (the
    rich version: Slack mention + channel invite for Slack logins, profile
    link + Slack-join email CTA for non-Slack logins, npo suffix), with
    identity via the 3-tier resolver so a broken OAuth token can't 404 the
    toggle. Returns a plain dict, or None when identity can't be resolved.
    """
    info(logger, "save_helping_status", propel_user_id=propel_user_id, data=d)

    user, user_id = users_service._resolve_and_ensure_user(propel_user_id)
    if user is None or not getattr(user, "id", None):
        warning(logger, "Could not resolve user for helping toggle", propel_user_id=propel_user_id)
        return None

    helping_status = d["status"]  # helping or not_helping
    problem_statement_id = d["problem_statement_id"]
    mentor_or_hacker = d["type"]
    npo_id = d.get("npo_id", "")

    to_add = {
        "user": user.id,
        "slack_user": user.user_id,
        "type": mentor_or_hacker,
        "timestamp": datetime.now().isoformat(),
    }

    db = get_db()
    problem_statement_doc = db.collection('problem_statements').document(problem_statement_id)
    ps_dict = problem_statement_doc.get().to_dict()
    # Missing doc: real Firestore yields None, MockFirestore yields {} — treat both as unknown
    if not ps_dict:
        warning(logger, "Helping toggle on unknown problem statement", problem_statement_id=problem_statement_id)
        return None

    helping_list = [h for h in ps_dict.get("helping", []) if isinstance(h, dict)]
    if "helping" == helping_status:
        mine = [h for h in helping_list if h.get("user") == user.id]
        if mine:
            # Already helping: switch role in place and keep the ORIGINAL
            # timestamp (their "helping since"). The legacy append-on-every-
            # click is why real docs carry the same person two or three times;
            # collapse those duplicates while we're here.
            mine.sort(key=lambda h: (h.get("timestamp") is None, h.get("timestamp") or ""))
            kept = mine[0]
            kept["type"] = mentor_or_hacker
            kept["slack_user"] = user.user_id
            helping_list = [h for h in helping_list if h.get("user") != user.id] + [kept]
            to_add = kept
        else:
            helping_list.append(to_add)
    else:
        # NOTE: the legacy body used `d['user'] not in user.id` — a substring
        # test that could remove other users' entries. Exact match only.
        helping_list = [h for h in helping_list if h.get('user') != user.id]

    problem_statement_doc.update({"helping": helping_list})
    clear_helpers_cache(problem_statement_id)

    # Project pages read helping through the messages-side caches
    try:
        from api.messages import messages_service
        messages_service.clear_cache()
    except Exception as e:
        warning(logger, "Failed to clear messages caches after helping toggle", error=str(e))

    try:
        send_slack_audit(action="helping", message=user.user_id, payload=to_add)
    except Exception:
        pass

    # Determine how to identify this user in the Slack post.
    # Slack logins get a real <@Uxxx> mention + auto-invite to the project
    # channel. Non-Slack logins (Google, etc.) fall back to their display name
    # so we don't render a broken "@oauth2" mention, and get a follow-up email
    # asking them to join the Slack workspace.
    display_name = (user.name or user.nickname or user.email_address or "A volunteer").strip()
    profile_url = f"https://ohack.dev/profile/{user.id}" if user.id else None

    if is_slack_user_id(user.user_id):
        slack_user_id = extract_slack_user_id(user.user_id)
        mention = f"<@{slack_user_id}> (<{profile_url}|profile>)" if profile_url else f"<@{slack_user_id}>"
        is_slack_login = True
    else:
        slack_user_id = None
        mention = f"<{profile_url}|{display_name}>" if profile_url else display_name
        is_slack_login = False

    problem_statement_title = ps_dict.get("title", "")

    if "slack_channel" in ps_dict:
        problem_statement_slack_channel = ps_dict["slack_channel"]

        project_link = f"<https://ohack.dev/project/{problem_statement_id}|{problem_statement_title}>"
        suffix = f" for <https://ohack.dev/nonprofit/{npo_id}|the nonprofit>" if npo_id else ""

        if "helping" == helping_status:
            slack_message = f"{mention} is helping as a *{mentor_or_hacker}* on *{project_link}*{suffix}"
        else:
            slack_message = f"{mention} is _no longer able to help_ on *{project_link}*{suffix}"

        if is_slack_login and slack_user_id:
            try:
                invite_user_to_channel(user_id=slack_user_id,
                                       channel_name=problem_statement_slack_channel)
            except Exception as e:
                warning(logger, "invite_user_to_channel failed", slack_user_id=slack_user_id, error=str(e))

        try:
            send_slack(message=slack_message, channel=problem_statement_slack_channel)
        except Exception as e:
            warning(logger, "helping Slack post failed", error=str(e))

    # For non-Slack users signing up to help, email them a Slack join CTA so
    # their project team can actually reach them. Swallow errors so a Resend
    # outage never breaks the help toggle.
    if not is_slack_login and helping_status == "helping" and user.email_address:
        try:
            from services.email_service import send_project_help_slack_invite_email
            send_project_help_slack_invite_email(
                name=user.name or user.nickname,
                email=user.email_address,
                problem_statement_title=ps_dict.get("title"),
                mentor_or_hacker=mentor_or_hacker,
                npo_id=npo_id or None,
                problem_statement_id=problem_statement_id,
            )
        except Exception as e:
            warning(logger, "send_project_help_slack_invite_email failed", error=str(e))

    return {"message": "Updated helping status"}


# ---------------------------------------------------------------------------
# "Who's helping" roster (frontend issue #359)
#
# The raw `helping` array on a problem statement is append-only history:
# {user: <db id>, slack_user: <oauth id>, type: hacker|mentor, timestamp}.
# The project page wants one row per person with a name/avatar and the date
# they first raised their hand, so this collapses duplicates and batch-loads
# the user docs in a single get_all. Public, PII-free (same fields the team
# rosters already expose), cached briefly and cleared on every toggle.
# ---------------------------------------------------------------------------
HELPERS_CACHE_TTL = 60
HELPER_TYPES = ("hacker", "mentor")
_helpers_cache: TTLCache = TTLCache(maxsize=512, ttl=HELPERS_CACHE_TTL)
_helpers_cache_lock = threading.Lock()


def clear_helpers_cache(problem_statement_id=None):
    with _helpers_cache_lock:
        if problem_statement_id is None:
            _helpers_cache.clear()
        else:
            _helpers_cache.pop(hashkey(problem_statement_id), None)


def _helping_timestamp(entry):
    ts = entry.get("timestamp")
    return ts if isinstance(ts, str) and ts else None


def normalize_helping_entries(helping):
    """Collapse raw helping entries into one record per person.

    Keeps the EARLIEST timestamp as `since` (when they first signed up) and
    the LATEST type (hacker/mentor) so a role switch is reflected. Entries
    without a user or slack_user are dropped. Result is oldest-first.
    """
    by_key = {}
    for entry in helping or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("user") or entry.get("slack_user")
        if not key:
            continue
        ts = _helping_timestamp(entry)
        etype = entry.get("type") if isinstance(entry.get("type"), str) and entry.get("type") else None
        rec = by_key.get(key)
        if rec is None:
            by_key[key] = {
                "db_id": entry.get("user") or None,
                "user_id": entry.get("slack_user") or None,
                "type": etype,
                "since": ts,
                "_latest": ts,
            }
            continue
        if ts and (rec["since"] is None or ts < rec["since"]):
            rec["since"] = ts
        if etype and (ts is None and rec["_latest"] is None or ts and (rec["_latest"] is None or ts >= rec["_latest"])):
            rec["type"] = etype
        if ts and (rec["_latest"] is None or ts >= rec["_latest"]):
            rec["_latest"] = ts
        if not rec["db_id"] and entry.get("user"):
            rec["db_id"] = entry["user"]
        if not rec["user_id"] and entry.get("slack_user"):
            rec["user_id"] = entry["slack_user"]

    records = []
    for rec in by_key.values():
        rec.pop("_latest", None)
        records.append(rec)
    records.sort(key=lambda r: (r["since"] is None, r["since"] or ""))
    return records


def _enrich_helpers_batch(records, db):
    """Attach name/nickname/profile_image from the users collection (one get_all)."""
    refs = {}
    for rec in records:
        db_id = rec.get("db_id")
        if db_id and db_id not in refs:
            refs[db_id] = db.collection("users").document(db_id)

    profiles = {}
    if refs:
        try:
            for snap in db.get_all(list(refs.values())):
                if getattr(snap, "exists", False):
                    d = snap.to_dict() or {}
                    profiles[snap.id] = {
                        "name": d.get("name"),
                        "nickname": d.get("nickname"),
                        "profile_image": d.get("profile_image"),
                        "user_id": d.get("user_id"),
                    }
        except Exception as e:
            warning(logger, "helpers get_all failed; returning roster without names", error=str(e))

    for rec in records:
        prof = profiles.get(rec.get("db_id")) or {}
        rec["name"] = prof.get("name")
        rec["nickname"] = prof.get("nickname")
        rec["profile_image"] = prof.get("profile_image")
        if not rec.get("user_id") and prof.get("user_id"):
            rec["user_id"] = prof["user_id"]
    return records


def get_problem_statement_helpers(problem_statement_id):
    """Public roster for a project's "Who's helping" panel.

    Returns None when the problem statement doesn't exist, else
    {problem_statement_id, slack_channel, helpers: [...], counts: {hacker, mentor, total}}.
    """
    key = hashkey(problem_statement_id)
    with _helpers_cache_lock:
        hit = _helpers_cache.get(key)
    if hit is not None:
        return hit

    db = get_db()
    snap = db.collection("problem_statements").document(problem_statement_id).get()
    ps = snap.to_dict() if snap is not None else None
    if not ps:
        return None

    records = _enrich_helpers_batch(normalize_helping_entries(ps.get("helping")), db)
    counts = {"hacker": 0, "mentor": 0, "total": len(records)}
    for rec in records:
        if rec.get("type") in counts:
            counts[rec["type"]] += 1

    result = {
        "problem_statement_id": problem_statement_id,
        "slack_channel": ps.get("slack_channel"),
        "helpers": records,
        "counts": counts,
    }
    with _helpers_cache_lock:
        _helpers_cache[key] = result
    return result


@limits(calls=100, period=ONE_MINUTE)
def link_problem_statements_to_events(json):    
    # JSON should be in the format of
    # {
    #   'mapping': {'problem_statement_id': 'd5c9426e0c4d11f0b7ec0af23886a873', 'event_id': 'cF9a64EwbmGmQ1YySSLE'}
    # }
    debug(logger, "Linking payload", payload=json)    
    
    data = json["mapping"]
    
    # Handle single mapping object format
    if isinstance(data, dict) and 'problem_statement_id' in data and 'event_id' in data:
        problem_statement_id = data['problem_statement_id']
        event_id = data['event_id']
        
        problem_statement = fetch_problem_statement(problem_statement_id)
        
        if problem_statement is not None:
            info(logger, "Checking event", event=event_id)
            
            hackathon = fetch_hackathon(event_id)
            hackathons = [hackathon] if hackathon else []

            update_problem_statement_hackathons(problem_statement, hackathons)
            return fetch_problem_statement(problem_statement.id)
    else:
        warning(logger, "Problem statement not found", id=problem_statement_id)
        return None
   