"""
Hackers' Choice — an assigned-slate approval vote (Sep 2026). See
docs/plans/team-dashboard-devpost-replacement.md (frontend repo) Part 2.5 for
the product rationale (anti-popularity: exposure-balanced slates, approval
not ranking, no visible tallies) and Part 3 for the wire contract.

Judging is completely untouched by this module — Hackers' Choice is a
SEPARATE peer-vote award, not a judging round.

Design notes:
- Each eligible voter (isSelected hacker) gets a deterministic slate of
  `peer_vote_slate_size` submitted projects, never their own team, built once
  and persisted on first GET (so re-opening the page always shows the same
  slate). The seed is sha256(f"{event_id}:{propel_id}") so it's stable across
  requests without needing to store anything before the first GET.
- Exposure counts (how many times each team has been shown) bias the slate
  toward the least-shown projects, so a small early team isn't buried by a
  few loud/popular ones.
- Scoring is the Wilson score interval LOWER bound (z=1.96) of
  approvals/shown, not a raw approval rate — a team shown to 2 people who both
  approved should not outrank a team shown to 40 people with a 90% approval
  rate. No tallies are ever shown to voters, only to admins (get_results).
"""
import hashlib
import logging
import math
import random
from collections import Counter
from datetime import datetime, timezone

from firebase_admin import firestore

from db.db import get_db
from common.utils.firestore_helpers import clear_all_caches
# NOTE: common.utils.slack calls load_dotenv() at import time, which
# populates FIREBASE_CERT_CONFIG (among others) from .env into the process
# environment. common.utils.firebase reads that var at ITS OWN import time
# (module-level, no lazy fallback) — importing it before anything has called
# load_dotenv() raises a JSONDecodeError on a real deployment that relies on
# .env. Every existing service that imports both (e.g. api/mentors/
# mentors_service.py) imports slack first for this reason; keep this order.
from common.utils.slack import send_slack_audit
from common.utils.firebase import get_hackathon_by_event_id
from common.utils.validators import normalize_deadline_iso
from services.teams_service import get_team

logger = logging.getLogger("myapp")

PEER_VOTES_COLLECTION = "peer_votes"
AWARD_NAME = "Hackers' Choice"
DEFAULT_SLATE_SIZE = 5
DEFAULT_MAX_PICKS = 2

# Mirror of api.submissions.submissions_service.SUBMITTED_STATUSES — a team
# must have a real write-up before it can appear in anyone's slate or get
# voted on. Duplicated (not imported) to keep the two blueprints
# one-directionally independent; the values are the small, stable
# project_submission_status catalog.
SUBMITTED_STATUSES = {"submitted", "late"}


def clear_cache() -> None:
    """Mirrors api/mentors/mentors_service.py's clear_cache(): bust every
    registered cache (including services.teams_service._GET_TEAM_CACHE) plus
    the hackathon event cache, since publish_results changes a team's
    `awards` array that the event page also renders."""
    clear_all_caches()
    try:
        from services.hackathons_service import clear_cache as clear_hackathon_caches
        clear_hackathon_caches()
    except Exception as e:  # pragma: no cover - best-effort cache bust
        logger.warning("peer_votes clear_cache: hackathon cache clear failed: %s", e)


def _ballot_doc_id(event_id, propel_id):
    safe_propel_id = (propel_id or "").replace("/", "_")
    return f"{event_id}__{safe_propel_id}"


def _peer_vote_subdoc(db, hackathon_doc_id, name):
    return db.collection("hackathons").document(hackathon_doc_id).collection("peer_vote").document(name)


def _settings(event):
    """{"enabled", "slate_size", "max_picks", "requires_submission"} — reads
    constraints.peer_vote_* off the hackathon doc with the Part 3 defaults.
    MUST read peer_vote_enabled (a disabled event returns status:"disabled"
    from every voter-facing route regardless of anything else)."""
    constraints = (event or {}).get("constraints") or {}
    return {
        "enabled": bool(constraints.get("peer_vote_enabled", False)),
        "slate_size": constraints.get("peer_vote_slate_size") or DEFAULT_SLATE_SIZE,
        "max_picks": constraints.get("peer_vote_max_picks") or DEFAULT_MAX_PICKS,
        "requires_submission": bool(constraints.get("peer_vote_requires_submission", False)),
    }


def compute_voting_window(event, now=None):
    """{"state": upcoming|open|closed, "opens_at", "closes_at"}.

    Defaults when unset: opens_at = deadlines.voting_opens, falling back to
    late_submission_until, falling back to submission; closes_at =
    deadlines.voting_closes, falling back to the event's end_date at
    23:59:59 in the event timezone. No usable opens_at/closes_at at all
    (a brand new event with no deadlines configured) -> closed, never open.
    """
    event = event or {}
    tz_name = event.get("timezone") or "America/Phoenix"
    now_dt = now or datetime.now(timezone.utc)
    deadlines = event.get("deadlines") or {}

    opens_at = deadlines.get("voting_opens") or deadlines.get("late_submission_until") or deadlines.get("submission")
    closes_at = deadlines.get("voting_closes")
    if not closes_at:
        end_date = event.get("end_date")
        if end_date:
            try:
                closes_at = normalize_deadline_iso(f"{end_date}T23:59:59", tz_name)
            except ValueError:
                closes_at = None

    if not opens_at or not closes_at:
        return {"state": "closed", "opens_at": opens_at, "closes_at": closes_at}

    opens_dt = datetime.fromisoformat(opens_at)
    closes_dt = datetime.fromisoformat(closes_at)
    if now_dt < opens_dt:
        state = "upcoming"
    elif now_dt <= closes_dt:
        state = "open"
    else:
        state = "closed"
    return {"state": state, "opens_at": opens_at, "closes_at": closes_at}


def _voter_eligibility(propel_id, event_id, event):
    """(eligible, volunteer|None, reason|None). Eligible = an isSelected
    hacker volunteer record for this event; when peer_vote_requires_submission
    is on, the voter's own team must also have submitted."""
    from services.volunteers_service import find_volunteer_by_caller_identity

    volunteer = find_volunteer_by_caller_identity(propel_id, event_id, "hacker")
    if not volunteer or not volunteer.get("isSelected"):
        return False, None, "not_selected_hacker"

    settings = _settings(event)
    if settings["requires_submission"]:
        own_ids = _own_team_ids(propel_id, event_id)
        submitted = False
        if own_ids:
            db = get_db()
            refs = [db.collection("teams").document(tid) for tid in own_ids]
            for snap in db.get_all(refs):
                if snap.exists and (snap.to_dict() or {}).get("project_submission_status") in SUBMITTED_STATUSES:
                    submitted = True
                    break
        if not submitted:
            return False, volunteer, "own_team_not_submitted"

    return True, volunteer, None


def _submitted_teams_for_event(event_id):
    """Active, submitted/late teams for the event. Single equality query +
    Python filter (no composite index needed)."""
    db = get_db()
    docs = db.collection("teams").where("hackathon_event_id", "==", event_id).stream()
    teams = []
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("active") is False:
            continue
        if data.get("project_submission_status") not in SUBMITTED_STATUSES:
            continue
        data["id"] = doc.id
        teams.append(data)
    return teams


def _own_team_ids(propel_id, event_id):
    from api.teams.teams_service import get_my_teams_by_event_id

    result = get_my_teams_by_event_id(propel_id, event_id) or {}
    return {t["id"] for t in result.get("teams", []) if t.get("id")}


def build_slate(candidates, exposure, event_id, propel_id, n):
    """Deterministic per-voter shuffle (seeded on event_id+propel_id) then a
    STABLE sort by current exposure ascending, so the least-shown candidates
    win ties in the same order every time this voter is shown a slate.
    `candidates` is a list of team dicts with an "id" key; returns a list of
    team ids, capped at n."""
    seed = int(hashlib.sha256(f"{event_id}:{propel_id}".encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    shuffled.sort(key=lambda c: exposure.get(c["id"], 0))
    return [c["id"] for c in shuffled[:n]]


def _in_transaction(db, body):
    """Runs body(transaction) inside a real Firestore transaction. Tests
    monkeypatch this directly (to `lambda db, body: body(FakeTx())`) since a
    real @firestore.transactional callable needs a live Firestore client."""
    @firestore.transactional
    def _run(transaction):
        return body(transaction)

    return _run(db.transaction())


def _tx_get_one(transaction, ref):
    """First (only) snapshot from transaction.get(ref) — the real
    google-cloud-firestore Transaction.get() returns a generator for a
    DocumentReference (it delegates to client.get_all())."""
    return next(iter(transaction.get(ref)), None)


def _slate_team_view(team):
    return {
        "team_id": team.get("id"),
        "name": team.get("name"),
        "project_tagline": team.get("project_tagline"),
        "project_thumbnail_url": team.get("project_thumbnail_url"),
        "demo_video_url": team.get("demo_video_url"),
        "github_links": team.get("github_links") or [],
        "users_count": len(team.get("users") or []),
    }


def _hydrate_slate(team_ids, candidates_by_id=None):
    candidates_by_id = candidates_by_id or {}
    views = []
    for tid in team_ids:
        team = candidates_by_id.get(tid)
        if team is None:
            team = (get_team(tid) or {}).get("team") or {"id": tid}
        views.append(_slate_team_view(team))
    return views


def _slate_response_from_ballot(ballot, window, settings, own_team_ids, candidates_by_id=None):
    picks = ballot.get("picks")
    status = "voted" if picks else window["state"]
    return {
        "status": status,
        "opens_at": window["opens_at"],
        "closes_at": window["closes_at"],
        "max_picks": settings["max_picks"],
        "slate": _hydrate_slate(ballot.get("slate") or [], candidates_by_id),
        "picks": picks,
        "own_team_ids": list(own_team_ids),
    }


def get_slate(propel_id, event_id):
    """GET /api/hackathons/<event_id>/peer-vote/slate.

    A ballot doc is persisted the FIRST time a voter's slate is materialized
    (inside a transaction, re-checking existence to survive a double-click /
    double-request race) and never rebuilt after that — re-opening the page
    always shows the same slate, and exposure is only incremented once.
    """
    event = get_hackathon_by_event_id(event_id)
    settings = _settings(event or {})
    if not event or not settings["enabled"]:
        return {"status": "disabled"}

    window = compute_voting_window(event)
    eligible, _volunteer, reason = _voter_eligibility(propel_id, event_id, event)
    if not eligible:
        return {"status": "not_eligible", "reason": reason, "opens_at": window["opens_at"], "closes_at": window["closes_at"], "max_picks": settings["max_picks"]}

    db = get_db()
    event_doc_id = event.get("id") or event_id
    ballot_ref = db.collection(PEER_VOTES_COLLECTION).document(_ballot_doc_id(event_id, propel_id))
    own_team_ids = _own_team_ids(propel_id, event_id)

    existing = ballot_ref.get()
    if existing.exists:
        return _slate_response_from_ballot(existing.to_dict() or {}, window, settings, own_team_ids)

    if window["state"] == "upcoming":
        return {"status": "upcoming", "opens_at": window["opens_at"], "closes_at": window["closes_at"], "max_picks": settings["max_picks"], "own_team_ids": list(own_team_ids)}
    if window["state"] == "closed":
        return {"status": "closed", "opens_at": window["opens_at"], "closes_at": window["closes_at"], "max_picks": settings["max_picks"], "own_team_ids": list(own_team_ids)}

    candidates = [t for t in _submitted_teams_for_event(event_id) if t["id"] not in own_team_ids]
    if len(candidates) < 2:
        return {
            "status": "open",
            "opens_at": window["opens_at"],
            "closes_at": window["closes_at"],
            "max_picks": settings["max_picks"],
            "slate": [],
            "own_team_ids": list(own_team_ids),
            "reason": "not_enough_submissions",
        }

    candidates_by_id = {c["id"]: c for c in candidates}
    exposure_ref = _peer_vote_subdoc(db, event_doc_id, "exposure")

    def _persist(transaction):
        tx_existing = _tx_get_one(transaction, ballot_ref)
        if tx_existing is not None and tx_existing.exists:
            return tx_existing.to_dict() or {}

        exposure_snap = _tx_get_one(transaction, exposure_ref)
        exposure = (exposure_snap.to_dict() or {}).get("counts", {}) if exposure_snap is not None and exposure_snap.exists else {}
        slate_ids = build_slate(candidates, exposure, event_id, propel_id, settings["slate_size"])
        now_iso = datetime.now(timezone.utc).isoformat()
        ballot_data = {
            "event_id": event_id,
            "voter_propel_id": propel_id,
            "slate": slate_ids,
            "shown_at": now_iso,
            "picks": None,
            "voted_at": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "voided": False,
        }
        transaction.set(ballot_ref, ballot_data)
        transaction.set(exposure_ref, {"counts": {tid: firestore.Increment(1) for tid in slate_ids}}, merge=True)
        return ballot_data

    ballot_data = _in_transaction(db, _persist)
    return _slate_response_from_ballot(ballot_data, window, settings, own_team_ids, candidates_by_id=candidates_by_id)


def submit_ballot(propel_id, event_id, picks):
    """POST /api/hackathons/<event_id>/peer-vote/ballot. Re-votable until
    close — a full-doc set() replaces `picks`, keeping the original
    `voted_at` (first vote only)."""
    event = get_hackathon_by_event_id(event_id)
    settings = _settings(event or {})
    if not event or not settings["enabled"]:
        return {"error": "peer_vote_disabled"}, 403

    window = compute_voting_window(event)
    eligible, _volunteer, reason = _voter_eligibility(propel_id, event_id, event)
    if not eligible:
        return {"error": "not_eligible", "reason": reason}, 403

    db = get_db()
    ballot_ref = db.collection(PEER_VOTES_COLLECTION).document(_ballot_doc_id(event_id, propel_id))
    snap = ballot_ref.get()
    if not snap.exists:
        return {"error": "no_slate"}, 400
    ballot = snap.to_dict() or {}

    if ballot.get("voided"):
        return {"error": "ballot_voided"}, 409
    if window["state"] == "closed":
        return {"error": "voting_closed"}, 409

    slate = ballot.get("slate") or []
    max_picks = settings["max_picks"]
    if (
        not isinstance(picks, list)
        or not picks
        or len(picks) > max_picks
        or len(set(picks)) != len(picks)
        or any(p not in slate for p in picks)
    ):
        return {"error": "invalid_picks"}, 400

    now_iso = datetime.now(timezone.utc).isoformat()
    update = {"picks": picks, "updated_at": now_iso}
    if not ballot.get("voted_at"):
        update["voted_at"] = now_iso
    ballot_ref.set(update, merge=True)

    send_slack_audit(
        action="peer_vote_ballot",
        message=f"Hackers' Choice ballot recorded for event {event_id}",
        payload={"event_id": event_id, "picks": picks},
    )
    return {"success": True, "picks": picks}, 200


def wilson_lower_bound(approvals, shown, z=1.96):
    """Wilson score interval LOWER bound for a binomial proportion —
    approvals/shown, penalized for a small sample. wilson_lower_bound(0,0)==0;
    (5,5)≈0.566; (1,1)≈0.207."""
    if not shown:
        return 0.0
    n = float(shown)
    p = approvals / n
    denom = 1 + (z * z) / n
    center = p + (z * z) / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n)
    return (center - margin) / denom


def compute_results(ballots, teams_by_id, exposure):
    """Pure. Ranks by Wilson lower bound desc, then raw approvals desc, then
    name — so a tie only ever breaks toward the more-approved, then
    alphabetically (stable, no hidden randomness in the admin view)."""
    approvals = Counter()
    for ballot in ballots:
        if ballot.get("voided"):
            continue
        for team_id in (ballot.get("picks") or []):
            approvals[team_id] += 1

    results = []
    for team_id, team in teams_by_id.items():
        shown = exposure.get(team_id, 0)
        approved = approvals.get(team_id, 0)
        results.append({
            "team_id": team_id,
            "name": team.get("name"),
            "shown": shown,
            "exposure_shown": shown,
            "approvals": approved,
            "approval_rate": (approved / shown) if shown else 0.0,
            "wilson_lower_bound": wilson_lower_bound(approved, shown),
        })

    results.sort(key=lambda r: (-r["wilson_lower_bound"], -r["approvals"], r["name"] or ""))
    for i, r in enumerate(results, start=1):
        r["rank"] = i
    return results


def get_results(event_id):
    """GET /api/hackathons/<event_id>/peer-vote/results (admin)."""
    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"error": "Event not found"}, 404

    settings = _settings(event)
    window = compute_voting_window(event)
    event_doc_id = event.get("id") or event_id

    db = get_db()
    all_ballots = [d.to_dict() or {} for d in db.collection(PEER_VOTES_COLLECTION).where("event_id", "==", event_id).stream()]
    voided_ballots = [b for b in all_ballots if b.get("voided")]
    active_ballots = [b for b in all_ballots if not b.get("voided")]

    exposure_snap = _peer_vote_subdoc(db, event_doc_id, "exposure").get()
    exposure = (exposure_snap.to_dict() or {}).get("counts", {}) if exposure_snap.exists else {}

    teams_by_id = {t["id"]: t for t in _submitted_teams_for_event(event_id)}
    for team_id in exposure.keys():
        if team_id not in teams_by_id:
            teams_by_id[team_id] = (get_team(team_id) or {}).get("team") or {"id": team_id}

    results = compute_results(active_ballots, teams_by_id, exposure)

    from services.volunteers_service import get_all_hackers_by_event_id
    eligible_estimate = sum(1 for h in get_all_hackers_by_event_id(event_id) if h.get("isSelected"))

    summary_exists = _peer_vote_subdoc(db, event_doc_id, "summary").get().exists

    return {
        "ballots": len(active_ballots),
        "voided": len(voided_ballots),
        "eligible_estimate": eligible_estimate,
        "window": window,
        "settings": settings,
        "published": summary_exists,
        "teams": results,
    }, 200


def void_ballot(event_id, voter_propel_id, actor):
    """POST /api/hackathons/<event_id>/peer-vote/ballots/<propel_id>/void (admin)."""
    db = get_db()
    ref = db.collection(PEER_VOTES_COLLECTION).document(_ballot_doc_id(event_id, voter_propel_id))
    snap = ref.get()
    if not snap.exists:
        return {"error": "Ballot not found"}, 404

    now_iso = datetime.now(timezone.utc).isoformat()
    ref.set({"voided": True, "voided_at": now_iso, "voided_by": actor}, merge=True)
    send_slack_audit(
        action="peer_vote_void",
        message=f"Hackers' Choice ballot voided for event {event_id}",
        payload={"event_id": event_id, "voter_propel_id": voter_propel_id, "by": actor},
    )
    return {"success": True}, 200


def publish_results(event_id, actor, team_id=None):
    """POST /api/hackathons/<event_id>/peer-vote/publish (admin). Idempotent:
    re-publishing the same winner never appends AWARD_NAME twice. `team_id`
    lets an admin override the computed rank-1 winner (e.g. a tie broken by
    judgment); defaults to rank 1."""
    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"error": "Event not found"}, 404

    results_payload, status = get_results(event_id)
    if status != 200:
        return results_payload, status

    ranked = results_payload.get("teams") or []
    if not ranked:
        return {"error": "no_ballots"}, 409

    winner_id = team_id or ranked[0]["team_id"]
    winner = next((t for t in ranked if t["team_id"] == winner_id), None)
    if winner is None:
        return {"error": "Winning team not found in results"}, 400

    db = get_db()
    team_ref = db.collection("teams").document(winner_id)
    team_snap = team_ref.get()
    team_data = (team_snap.to_dict() or {}) if team_snap.exists else {}
    awards = list(team_data.get("awards") or [])
    if AWARD_NAME not in awards:
        awards.append(AWARD_NAME)
        team_ref.set({"awards": awards}, merge=True)

    now_iso = datetime.now(timezone.utc).isoformat()
    winner_name = winner.get("name") or team_data.get("name")
    _peer_vote_subdoc(db, event.get("id") or event_id, "summary").set({
        "winner_team_id": winner_id,
        "winner_team_name": winner_name,
        "published_at": now_iso,
        "published_by": actor,
        "ballots": results_payload.get("ballots", 0),
    })

    send_slack_audit(
        action="peer_vote_publish",
        message=f"Hackers' Choice published for event {event_id}: {winner_name} ({winner_id})",
        payload={"event_id": event_id, "winner_team_id": winner_id},
    )
    clear_cache()

    return {"success": True, "winner_team_id": winner_id, "winner_team_name": winner_name}, 200


def get_public_summary(event_id):
    """GET /api/hackathons/<event_id>/peer-vote/summary (public)."""
    event = get_hackathon_by_event_id(event_id)
    if not event:
        return {"published": False}, 200

    db = get_db()
    summary_snap = _peer_vote_subdoc(db, event.get("id") or event_id, "summary").get()
    if not summary_snap.exists:
        return {"published": False}, 200

    data = summary_snap.to_dict() or {}
    return {
        "published": True,
        "winner_team_id": data.get("winner_team_id"),
        "winner_team_name": data.get("winner_team_name"),
        "published_at": data.get("published_at"),
        "ballots": data.get("ballots"),
    }, 200
