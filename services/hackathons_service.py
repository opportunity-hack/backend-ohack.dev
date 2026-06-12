import uuid
import os
import random
import threading
from collections import Counter
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
from common.utils.redis_cache import get_cached, set_cached
from common.utils.validators import validate_hackathon_data_partial
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
    """Clear all hackathon-related caches (in-process + Redis)."""
    from common.utils.redis_cache import delete_cached
    clear_all_caches()
    get_single_hackathon_event.cache_clear()
    get_single_hackathon_id.cache_clear()
    get_hackathon_list.cache_clear()
    get_hackathon_funnel.cache_clear()
    delete_cached(_FUNNEL_AGG_CACHE_KEY)


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

        logger.info(f"get_single_hackathon_id end (with result id={doc.id})")
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
        count = len(results.get("data", [])) if isinstance(results, dict) else 0
        logger.debug(f"get {volunteer_type} end ({count} results)")
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
        count = len(results.get("data", [])) if isinstance(results, dict) else 0
        logger.debug(f"get {volunteer_type} end ({count} results)")
        return results


@cached(cache=TTLCache(maxsize=200, ttl=300))
@limits(calls=2000, period=ONE_MINUTE)
def get_hackathon_funnel(event_id):
    """
    Return the "hacker funnel" for a hackathon: registered -> started ->
    submitted -> won -> founding engineers.

    The first three stages come from a public-safe, PII-free summary doc at
    hackathons/{doc_id}/funnel/summary (written by
    scripts/backfill_devpost_funnel.py). The last two are computed live from
    the teams collection so they're always fresh after judging changes.

    Returns:
      {
        "event_id": ...,
        "summary": {<devpost-derived counts>} | None,
        "winners": {
            "won_prize": int,             # any winning status
            "founding_engineers": int,    # 1st place
            "completion_support": int,    # 2nd place
            "category_winner": int,       # category winners
        },
        "teams_total": int,
      }
    """
    logger.debug(f"get_hackathon_funnel start event_id={event_id}")
    db = _get_db()

    docs = list(
        db.collection("hackathons").where(
            filter=firestore.FieldFilter("event_id", "==", event_id)
        ).stream()
    )
    if not docs:
        logger.warning(f"get_hackathon_funnel: hackathon event_id={event_id} not found")
        return {"event_id": event_id, "summary": None, "winners": None, "teams_total": 0}
    hackathon_doc = docs[0]
    hackathon_data = hackathon_doc.to_dict() or {}

    # Pull the devpost-derived summary, if it exists.
    summary_snap = (
        db.collection("hackathons")
        .document(hackathon_doc.id)
        .collection("funnel")
        .document("summary")
        .get()
    )
    summary = summary_snap.to_dict() if summary_snap.exists else None

    # Compute winner counts and team-membership counts from the team docs
    # linked to this hackathon. The funnel reports people (not teams), so
    # we dedupe user-doc IDs per stage. We also keep per-status TEAM counts
    # under `*_teams` keys for any consumer that wants them.
    team_refs = hackathon_data.get("teams") or []
    won_prize_teams = 0
    founding_engineers_teams = 0
    completion_support_teams = 0
    category_winner_teams = 0
    unique_team_member_ids = set()       # everyone on any team
    won_prize_member_ids = set()         # everyone on any winning team
    founding_engineers_member_ids = set()
    completion_support_member_ids = set()
    category_winner_member_ids = set()
    if team_refs:
        team_docs = db.get_all(team_refs)
        for td in team_docs:
            if not td.exists:
                continue
            td_dict = td.to_dict() or {}
            status = td_dict.get("status") or ""
            user_ids = [
                u_ref.id for u_ref in (td_dict.get("users") or [])
                if hasattr(u_ref, "id") and u_ref.id
            ]
            unique_team_member_ids.update(user_ids)
            if status == "FOUNDING_ENGINEERS":
                founding_engineers_teams += 1
                won_prize_teams += 1
                founding_engineers_member_ids.update(user_ids)
                won_prize_member_ids.update(user_ids)
            elif status == "COMPLETION_SUPPORT":
                completion_support_teams += 1
                won_prize_teams += 1
                completion_support_member_ids.update(user_ids)
                won_prize_member_ids.update(user_ids)
            elif status == "CATEGORY_WINNER":
                category_winner_teams += 1
                won_prize_teams += 1
                category_winner_member_ids.update(user_ids)
                won_prize_member_ids.update(user_ids)

    # Count hacker applicants (volunteers/{event_id} with volunteer_type=hacker).
    # Matches the count surfaced in HackathonResults.js (no isSelected filter
    # for hackers — every applicant counts).
    applied_as_hacker = 0
    try:
        applied_as_hacker = sum(
            1
            for _ in db.collection("volunteers")
            .where(filter=firestore.FieldFilter("event_id", "==", event_id))
            .where(filter=firestore.FieldFilter("volunteer_type", "==", "hacker"))
            .stream()
        )
    except Exception as e:
        logger.warning(f"get_hackathon_funnel: hacker count query failed: {e}")

    result = {
        "event_id": event_id,
        "summary": summary,
        "participation": {
            "applied_as_hacker": applied_as_hacker,
            "formed_team": len(unique_team_member_ids),
        },
        # All counts here are PEOPLE (deduped by user-doc id). Team counts
        # are provided alongside under *_teams for any consumer that wants
        # them, but the funnel UI uses the people counts.
        "winners": {
            "won_prize": len(won_prize_member_ids),
            "founding_engineers": len(founding_engineers_member_ids),
            "completion_support": len(completion_support_member_ids),
            "category_winner": len(category_winner_member_ids),
            "won_prize_teams": won_prize_teams,
            "founding_engineers_teams": founding_engineers_teams,
            "completion_support_teams": completion_support_teams,
            "category_winner_teams": category_winner_teams,
        },
        "teams_total": len(team_refs),
    }
    logger.info(
        f"get_hackathon_funnel end event_id={event_id} "
        f"applied_hacker={applied_as_hacker} formed_team={len(unique_team_member_ids)} "
        f"won={len(won_prize_member_ids)}p/{won_prize_teams}t "
        f"founding={len(founding_engineers_member_ids)}p/{founding_engineers_teams}t "
        f"teams={len(team_refs)} summary={'yes' if summary else 'no'}"
    )
    return result


_FUNNEL_AGG_CACHE_KEY = "funnel_aggregate:v1"
_FUNNEL_AGG_REDIS_TTL = 6 * 3600  # 6 hours — historical data rarely changes

@limits(calls=200, period=ONE_MINUTE)
def get_hackathon_funnel_aggregate():
    """
    Aggregate funnel across every hackathon. No cross-event dedup — a person
    on multiple events counts in each one, which is what we want for an
    "all-time" view.

    Returns the same shape as get_hackathon_funnel(), plus:
      - events_total: int           total hackathons iterated
      - events_with_summary: int    how many have a funnel/summary doc
      - events_with_winners: int    how many have at least one winning team

    Performance: caches in Redis (6h TTL) with in-process TTLCache fallback.
    On a cache miss, batches all funnel/summary sub-doc reads in one
    db.get_all() call and replaces per-event volunteers queries with a single
    global query + Counter.
    """
    cached_result = get_cached(_FUNNEL_AGG_CACHE_KEY)
    if cached_result is not None:
        logger.debug("get_hackathon_funnel_aggregate: Redis/local cache hit")
        return cached_result

    import time as _time
    _t0 = _time.time()
    logger.info("get_hackathon_funnel_aggregate: cache miss — recomputing")

    db = _get_db()

    agg_summary_keys = (
        "registered", "started_project", "submitted_project", "submitted_gallery_visible",
        "started_project_teams", "submitted_project_teams", "submitted_gallery_visible_teams",
    )
    agg_breakdown_keys = (
        "status_breakdown", "step_breakdown", "referral_breakdown",
        "teammate_intent_breakdown", "country_breakdown",
    )

    summed = {k: 0 for k in agg_summary_keys}
    breakdowns = {k: Counter() for k in agg_breakdown_keys}

    applied_as_hacker_total = 0
    formed_team_total = 0
    won_prize_total = 0
    founding_engineers_total = 0
    completion_support_total = 0
    category_winner_total = 0
    won_prize_teams_total = 0
    founding_engineers_teams_total = 0
    completion_support_teams_total = 0
    category_winner_teams_total = 0
    teams_total = 0

    events_total = 0
    events_with_summary = 0
    events_with_winners = 0

    # Step 1: Fetch all hackathon docs in one scan.
    hackathon_docs = list(db.collection("hackathons").stream())
    events_total = len(hackathon_docs)

    # Step 2: Batch-fetch all funnel/summary sub-docs in ONE round-trip.
    summary_refs = [
        db.collection("hackathons").document(h.id).collection("funnel").document("summary")
        for h in hackathon_docs
    ]
    summary_snaps = list(db.get_all(summary_refs)) if summary_refs else []
    summary_by_hackathon_id = {
        snap.reference.parent.parent.id: snap
        for snap in summary_snaps
        if snap is not None
    }

    # Step 3: One global hacker query → Counter by event_id (replaces N per-event queries).
    try:
        hacker_counts = Counter(
            doc.to_dict().get("event_id")
            for doc in db.collection("volunteers")
            .where(filter=firestore.FieldFilter("volunteer_type", "==", "hacker"))
            .select(["event_id"])
            .stream()
            if doc.to_dict().get("event_id")
        )
    except Exception as e:
        logger.warning(f"get_hackathon_funnel_aggregate: global hacker count failed: {e}")
        hacker_counts = Counter()

    # Step 4: Process each hackathon with pre-fetched data.
    for hackathon_doc in hackathon_docs:
        h_data = hackathon_doc.to_dict() or {}
        event_id = h_data.get("event_id")

        summary_snap = summary_by_hackathon_id.get(hackathon_doc.id)
        if summary_snap and summary_snap.exists:
            events_with_summary += 1
            s = summary_snap.to_dict() or {}
            for k in agg_summary_keys:
                v = s.get(k)
                if isinstance(v, (int, float)):
                    summed[k] += int(v)
            for k in agg_breakdown_keys:
                bd = s.get(k) or {}
                if isinstance(bd, dict):
                    for label, count in bd.items():
                        if isinstance(count, (int, float)):
                            breakdowns[k][label] += int(count)

        # Team membership + winner counts (live, like per-event endpoint).
        team_refs = h_data.get("teams") or []
        if team_refs:
            team_docs = db.get_all(team_refs)
            event_winners = 0
            unique_member_ids = set()
            for td in team_docs:
                if not td.exists:
                    continue
                td_dict = td.to_dict() or {}
                status = td_dict.get("status") or ""
                user_ids = [
                    u.id for u in (td_dict.get("users") or [])
                    if hasattr(u, "id") and u.id
                ]
                unique_member_ids.update(user_ids)
                if status == "FOUNDING_ENGINEERS":
                    founding_engineers_teams_total += 1
                    won_prize_teams_total += 1
                    founding_engineers_total += len(set(user_ids))
                    won_prize_total += len(set(user_ids))
                    event_winners += 1
                elif status == "COMPLETION_SUPPORT":
                    completion_support_teams_total += 1
                    won_prize_teams_total += 1
                    completion_support_total += len(set(user_ids))
                    won_prize_total += len(set(user_ids))
                    event_winners += 1
                elif status == "CATEGORY_WINNER":
                    category_winner_teams_total += 1
                    won_prize_teams_total += 1
                    category_winner_total += len(set(user_ids))
                    won_prize_total += len(set(user_ids))
                    event_winners += 1
            formed_team_total += len(unique_member_ids)
            teams_total += len(team_refs)
            if event_winners:
                events_with_winners += 1

        if event_id:
            applied_as_hacker_total += hacker_counts.get(event_id, 0)

    aggregated_summary = {
        **summed,
        **{k: dict(v) for k, v in breakdowns.items()},
        "source": "aggregate",
        "source_files": [],
        "last_updated": datetime.now().isoformat(),
        "last_updated_by": "services/hackathons_service.get_hackathon_funnel_aggregate",
    }

    result = {
        "event_id": "__aggregate__",
        "summary": aggregated_summary,
        "participation": {
            "applied_as_hacker": applied_as_hacker_total,
            "formed_team": formed_team_total,
        },
        "winners": {
            "won_prize": won_prize_total,
            "founding_engineers": founding_engineers_total,
            "completion_support": completion_support_total,
            "category_winner": category_winner_total,
            "won_prize_teams": won_prize_teams_total,
            "founding_engineers_teams": founding_engineers_teams_total,
            "completion_support_teams": completion_support_teams_total,
            "category_winner_teams": category_winner_teams_total,
        },
        "teams_total": teams_total,
        "events_total": events_total,
        "events_with_summary": events_with_summary,
        "events_with_winners": events_with_winners,
    }

    elapsed = _time.time() - _t0
    logger.info(
        f"get_hackathon_funnel_aggregate computed in {elapsed:.2f}s: "
        f"events={events_total} with_summary={events_with_summary} "
        f"with_winners={events_with_winners} won={won_prize_total}p "
        f"founding={founding_engineers_total}p teams={teams_total}"
    )

    set_cached(_FUNNEL_AGG_CACHE_KEY, result, ttl=_FUNNEL_AGG_REDIS_TTL)
    return result


def _enrich_teams_users_batch(teams, db):
    """
    Replace users[] on every team dict with slim profile dicts in a single
    batched Firestore get_all call (one round-trip for all teams combined).
    Mirrors services/teams_service.py::_enrich_team_users but operates on a
    list so the caller doesn't pay N round-trips.
    """
    uid_to_refs = {}  # user_id -> DocumentReference (deduped)
    team_user_ids = []  # parallel to teams: list of user_id lists per team
    for team in teams:
        if team is None:
            team_user_ids.append([])
            continue
        uids = [u for u in (team.get("users") or []) if isinstance(u, str)]
        team_user_ids.append(uids)
        for uid in uids:
            if uid not in uid_to_refs:
                uid_to_refs[uid] = db.collection("users").document(uid)

    if not uid_to_refs:
        return teams

    try:
        snapshots = db.get_all(list(uid_to_refs.values()))
        snap_by_id = {}
        for snap in snapshots:
            if snap.exists:
                d = snap.to_dict() or {}
                snap_by_id[snap.id] = {
                    "id": snap.id,
                    "user_id": d.get("user_id"),
                    "name": d.get("name"),
                    "nickname": d.get("nickname"),
                    "profile_image": d.get("profile_image"),
                }
            else:
                snap_by_id[snap.id] = {"id": snap.id, "user_id": None, "name": None, "nickname": None, "profile_image": None}
    except Exception as e:
        logger.warning(f"_enrich_teams_users_batch get_all failed, leaving user ids in place: {e}")
        return teams

    for team, uids in zip(teams, team_user_ids):
        team["users"] = [snap_by_id.get(uid, {"id": uid, "user_id": None, "name": None, "nickname": None, "profile_image": None}) for uid in uids]

    return teams


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
            teams = [t for t in (doc_to_json(doc=team, docid=team.id) for team in result["teams"]) if t is not None]
            result["teams"] = _enrich_teams_users_batch(teams, _get_db())
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
        # Return the full archive so /hack can show every past hackathon (back to 2014).
        # Year chips on the frontend handle navigation; pagination is client-side.
        logger.debug(f"Querying all previous events (end_date <= {today_str})")
        query = query.where("end_date", "<=", today_str)
        query = query.order_by("end_date", direction=firestore.Query.DESCENDING).limit(200)

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
        data, skipped_fields = validate_hackathon_data_partial(json_data)

        if skipped_fields:
            logger.warning("save_hackathon: %d field(s) skipped due to validation errors: %s", len(skipped_fields), skipped_fields)

        doc_id = data.get("id") or uuid.uuid1().hex
        is_update = "id" in json_data

        hackathon_data = {
            "title": data["title"],
            "description": data["description"],
            "location": data["location"],
            "start_date": data["start_date"],
            "end_date": data["end_date"],
            "type": data["type"],
            "image_url": data["image_url"],
            "event_id": data["event_id"],
            "links": data.get("links", []),
            "countdowns": data.get("countdowns", []),
            "constraints": data.get("constraints", {
                "max_people_per_team": 5,
                "max_teams_per_problem": 10,
                "min_people_per_team": 2,
            }),
            "donation_current": data.get("donation_current", {
                "food": "0",
                "prize": "0",
                "swag": "0",
                "thank_you": "",
            }),
            "donation_goals": data.get("donation_goals", {
                "food": "0",
                "prize": "0",
                "swag": "0",
            }),
            "timezone": data.get("timezone", "America/Phoenix"),
            "event_photos": data.get("event_photos", []),
            "social_posts": data.get("social_posts", []),
            "last_updated": firestore.SERVER_TIMESTAMP,
            "last_updated_by": propel_id,
        }

        if "planning" in data:
            hackathon_data["planning"] = data["planning"]

        if "nonprofits" in data:
            hackathon_data["nonprofits"] = [db.collection("nonprofits").document(npo) for npo in data["nonprofits"]]
        if "teams" in data:
            hackathon_data["teams"] = [db.collection("teams").document(team) for team in data["teams"]]
        if "visible_problem_statements" in data:
            hackathon_data["visible_problem_statements"] = data["visible_problem_statements"]

        # Optional top-level fields that pass straight through when present.
        # (Validated in validate_hackathon_data_partial; not part of the core
        # required set, so they need an explicit copy here or merge=True drops
        # them.)
        for optional_key in ("github_org", "mentor_slack_channel"):
            if optional_key in data:
                hackathon_data[optional_key] = data[optional_key]

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
        msg = Message("Saved Hackathon")
        if skipped_fields:
            msg.skipped_fields = skipped_fields
        return msg

    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        return {"error": str(ve)}, 400
    except Exception as e:
        logger.error(f"Error saving/updating hackathon: {str(e)}")
        return {"error": "An unexpected error occurred"}, 500


@limits(calls=50, period=ONE_MINUTE)
def update_hackathon_visible_problem_statements(json_data, propel_id):
    """Update the visible_problem_statements list for a hackathon."""
    db = _get_db()
    hackathon_id = json_data.get("hackathonId")
    problem_statement_ids = json_data.get("problemStatementIds", [])

    if not hackathon_id:
        return {"error": "hackathonId is required"}, 400

    try:
        hackathon_ref = db.collection('hackathons').document(hackathon_id)
        hackathon_ref.update({
            "visible_problem_statements": problem_statement_ids,
            "last_updated": firestore.SERVER_TIMESTAMP,
            "last_updated_by": propel_id,
        })

        clear_cache()

        logger.info(f"Updated visible_problem_statements for hackathon {hackathon_id}")
        return Message("Updated visible problem statements")
    except Exception as e:
        logger.error(f"Error updating visible_problem_statements: {str(e)}")
        return {"error": "An unexpected error occurred"}, 500
