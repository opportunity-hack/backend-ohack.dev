import logging
import re
import threading
import time
from typing import Dict, Any, List

from cachetools import TTLCache, cached
from db.db import get_db
from common.utils.github import get_all_repos
from common.utils.firebase import get_hackathon_by_event_id
from common.utils.redis_cache import get_cached, set_cached

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)

_LEADERBOARD_CACHE_TTL = 300  # 5 minutes
_MENTOR_OPPS_CACHE = TTLCache(maxsize=50, ttl=300)
_MENTOR_OPPS_LOCK = threading.Lock()

# Normalize season-YYYY -> YYYY_season aliases so legacy URLs don't 404.
# e.g. "summer-2025" -> "2025_summer"
_SEASON_YEAR_RE = re.compile(r"^(spring|summer|fall|winter)-(\d{4})$", re.IGNORECASE)


def _normalize_event_id(event_id: str) -> str:
    m = _SEASON_YEAR_RE.match(event_id or "")
    if m:
        return f"{m.group(2)}_{m.group(1).lower()}"
    return event_id


def get_github_organizations(event_id: str, hackathon: Dict = None) -> Dict[str, Any]:
    logger.debug("Getting GitHub organizations for event ID: %s", event_id)

    try:
        if hackathon is None:
            hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.info("Hackathon not found for event ID: %s", event_id)
            return {"github_organizations": []}

        org_name = hackathon.get("github_org")
        if not org_name:
            logger.warning("GitHub organization not found in hackathon data for event ID: %s", event_id)
            return {"github_organizations": []}

        return {
            "github_organizations": [
                {
                    "__id__": org_name,
                    "name": org_name
                }
            ]
        }
    except Exception as e:
        logger.error("Error getting GitHub organizations for event ID %s: %s", event_id, e)
        return {"github_organizations": []}

def get_github_repositories(org_name: str) -> Dict[str, Any]:
    logger.debug("Getting GitHub repositories for org_name: %s", org_name)

    if not org_name:
        logger.error("Organization name is empty or None")
        return {"github_repositories": []}

    organization_ref = get_db().collection('github_organizations').document(org_name)
    repos_ref = organization_ref.collection('github_repositories')
    logger.debug("Using organization reference: %s", organization_ref.id)

    try:
        if not organization_ref.get().exists:
            logger.warning("Organization %s not found in database", org_name)
            return {"github_repositories": []}

        repos = repos_ref.stream()

        if not repos:
            logger.warning("No repositories found for organization %s", org_name)
            return {"github_repositories": []}

        github_repositories = []
        for repo in repos:
            repo_data = repo.to_dict()
            if "__id__" not in repo_data:
                repo_data["__id__"] = repo.id
            repo_data["org_name"] = org_name
            github_repositories.append(repo_data)

        return {"github_repositories": github_repositories}

    except Exception as e:
        logger.error("Error getting repositories for organization %s: %s", org_name, e)
        return {"github_repositories": []}


def get_github_contributors(event_id: str, hackathon: Dict = None) -> Dict[str, Any]:
    logger.debug("Getting GitHub contributors for event ID: %s", event_id)

    try:
        if hackathon is None:
            hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.info("Hackathon not found for event ID: %s", event_id)
            return {"github_contributors": []}

        org_name = hackathon.get("github_org")
        if not org_name:
            logger.warning("GitHub organization not found in hackathon data for event ID: %s", event_id)
            return {"github_contributors": []}

        db = get_db()

        contributors_ref = db.collection_group('github_contributors').where("org_name", "==", org_name)
        docs = contributors_ref.stream()

        contributors = []
        for doc in docs:
            contribution = doc.to_dict()
            contribution["id"] = doc.id
            repo_ref = doc.reference.parent.parent
            org_ref = repo_ref.parent.parent
            contribution["repo_name"] = repo_ref.id
            contribution["org_name"] = org_ref.id
            contributors.append(contribution)

        return {"github_contributors": contributors}

    except Exception as e:
        logger.error("Error getting contributors for event ID %s: %s", event_id, e)
        return {"github_contributors": []}

def get_github_achievements(event_id: str, hackathon: Dict = None) -> List[Dict]:
    logger.debug("Getting GitHub achievements for event ID: %s", event_id)

    try:
        if hackathon is None:
            hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.info("Hackathon not found for event ID: %s", event_id)
            return []

        org_name = hackathon.get("github_org")
        if not org_name:
            logger.warning("GitHub organization not found in hackathon data for event ID: %s", event_id)
            return []

        db = get_db()

        organization_ref = db.collection('github_organizations').document(org_name)
        org_doc = organization_ref.get()
        if not org_doc.exists:
            logger.warning("Organization %s not found in database", org_name)
            return []

        achievements_ref = organization_ref.collection('achievements')
        docs = list(achievements_ref.stream())

        if not docs:
            logger.info("No achievements found for organization %s", org_name)
            return []

        achievements = []
        for doc in docs:
            achievement = doc.to_dict()
            if "__id__" not in achievement:
                achievement["__id__"] = doc.id
            achievements.append(achievement)

        logger.debug("Found %d achievements for organization %s", len(achievements), org_name)

        achievements.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return achievements

    except Exception as e:
        logger.error("Error getting achievements for event ID %s: %s", event_id, e, exc_info=True)
        return []

def calculate_general_stats(contributors: List[Dict]) -> List[Dict]:
    """Calculate general statistics based on contributor data."""
    total_commits = sum(c.get('commits', 0) for c in contributors)
    total_prs = sum(c.get('pull_requests', {}).get('merged', 0) for c in contributors)
    total_issues_closed = sum(c.get('issues', {}).get('closed', 0) for c in contributors)

    total_additions = sum(c.get('additions', 0) for c in contributors)
    total_deletions = sum(c.get('deletions', 0) for c in contributors)
    total_lines = total_additions - total_deletions if total_additions > 0 or total_deletions > 0 else 0

    unique_contributors = set()
    for c in contributors:
        if c.get('login'):
            unique_contributors.add(c.get('login'))

    stats = [
        {
            "stat": "GitHub Commits",
            "value": total_commits,
            "icon": "code",
            "description": "Total code commits during the hackathon"
        },
        {
            "stat": "Pull Requests",
            "value": total_prs,
            "icon": "merge",
            "description": "Merged pull requests across all teams"
        },
        {
            "stat": "Contributors",
            "value": len(unique_contributors),
            "icon": "group",
            "description": "Unique contributors across all repositories"
        },
        {
            "stat": "Issues Closed",
            "value": total_issues_closed,
            "icon": "task_alt",
            "description": "Issues closed during the hackathon"
        }
    ]

    if total_lines > 0:
        stats.append({
            "stat": "Lines of Code",
            "value": total_lines,
            "icon": "integration_instructions",
            "description": "Net lines of code added"
        })

    return stats

def categorize_individual_achievements(achievements: List[Dict]) -> List[Dict]:
    logger.debug("Starting categorize_individual_achievements with %d total achievements", len(achievements))

    individual_achievements = [a for a in achievements if "person" in a]
    logger.debug("Found %d individual achievements with 'person' field", len(individual_achievements))

    if not individual_achievements:
        logger.info("No individual achievements found. Sample of first achievements: %s",
                      str(achievements[:3]) if achievements else "[]")
        return []

    for i, achievement in enumerate(individual_achievements):
        logger.debug("Processing achievement %d: %s", i, achievement.get("__id__", "unknown_id"))

        if "icon" not in achievement:
            achievement["icon"] = "star"
        if "value" not in achievement:
            achievement["value"] = "-"
        if "description" not in achievement:
            achievement["description"] = achievement.get("title", "Achievement")

        if "person" in achievement:
            person = achievement["person"]
            if "name" not in person:
                person["name"] = person.get("githubUsername", "Unknown")
            if "avatar" not in person:
                person["avatar"] = f"https://avatars.githubusercontent.com/{person.get('githubUsername', 'unknown')}"
            if "team" not in person or not person["team"]:
                repo_name = achievement.get("repo", "")
                if "--" in repo_name:
                    person["team"] = "Team " + repo_name.split("--")[-1][:15]
                else:
                    person["team"] = repo_name

    priority_titles = ["Most Commits", "Epic PR", "First to Commit", "Night Owl"]

    try:
        sorted_achievements = sorted(
            individual_achievements,
            key=lambda x: (
                priority_titles.index(x.get("title", "")) if x.get("title", "") in priority_titles else 999,
                x.get("timestamp", "")
            )
        )
    except Exception as e:
        logger.error("Error sorting achievements: %s", e)
        sorted_achievements = individual_achievements

    result = sorted_achievements[:8]
    logger.debug("Returning %d individual achievements", len(result))
    return result

def categorize_team_achievements(achievements: List[Dict], contributors: List[Dict]) -> List[Dict]:
    team_achievements = [a for a in achievements if "team" in a and "person" not in a and a.get("type") != "mentor_opportunity"]

    if team_achievements:
        for achievement in team_achievements:
            if "icon" not in achievement:
                achievement["icon"] = "group"
            if "value" not in achievement:
                achievement["value"] = "-"
            if "description" not in achievement:
                achievement["description"] = achievement.get("title", "Team Achievement")
        return team_achievements[:4]

    teams_data = {}
    for contributor in contributors:
        repo_name = contributor.get('repo_name')
        if not repo_name:
            continue

        if repo_name not in teams_data:
            teams_data[repo_name] = {
                'commits': 0,
                'prs_merged': 0,
                'contributors': set(),
                'team_name': repo_name.split('--')[-1] if '--' in repo_name else repo_name
            }

        teams_data[repo_name]['commits'] += contributor.get('commits', 0)
        teams_data[repo_name]['prs_merged'] += contributor.get('pull_requests', {}).get('merged', 0)
        teams_data[repo_name]['contributors'].add(contributor.get('login'))

    teams_list = []
    for repo, data in teams_data.items():
        teams_list.append({
            'repo_name': repo,
            'team_name': data['team_name'],
            'commits': data['commits'],
            'prs_merged': data['prs_merged'],
            'contributor_count': len(data['contributors'])
        })

    most_productive = sorted(teams_list, key=lambda x: x['commits'], reverse=True)
    most_collaborative = sorted(teams_list, key=lambda x: x['prs_merged'], reverse=True)
    largest_team = sorted(teams_list, key=lambda x: x['contributor_count'], reverse=True)

    calculated_team_achievements = []

    if most_productive and most_productive[0]['commits'] > 0:
        calculated_team_achievements.append({
            "title": "Most Productive Team",
            "team": most_productive[0]['team_name'],
            "value": f"{most_productive[0]['commits']} commits",
            "icon": "code",
            "members": most_productive[0]['contributor_count'],
            "description": "Highest number of code commits during the hackathon",
            "teamPage": most_productive[0]['repo_name']
        })

    if most_collaborative and most_collaborative[0]['prs_merged'] > 0:
        calculated_team_achievements.append({
            "title": "Most Collaborative",
            "team": most_collaborative[0]['team_name'],
            "value": f"{most_collaborative[0]['prs_merged']} PRs merged",
            "icon": "merge",
            "members": most_collaborative[0]['contributor_count'],
            "description": "Highest number of pull requests merged",
            "teamPage": most_collaborative[0]['repo_name']
        })

    if largest_team and largest_team[0]['contributor_count'] > 0:
        calculated_team_achievements.append({
            "title": "Largest Team",
            "team": largest_team[0]['team_name'],
            "value": f"{largest_team[0]['contributor_count']} contributors",
            "icon": "group",
            "members": largest_team[0]['contributor_count'],
            "description": "Team with the most contributors",
            "teamPage": largest_team[0]['repo_name']
        })

    return calculated_team_achievements[:4]

def categorize_mentor_opportunities(achievements: List[Dict]) -> List[Dict]:
    opportunities = [a for a in achievements if a.get("type") == "mentor_opportunity"]

    for opp in opportunities:
        if "icon" not in opp:
            opp["icon"] = "rocket_launch"
        if "value" not in opp:
            opp["value"] = "-"
        if "description" not in opp:
            opp["description"] = "This team might benefit from some mentor guidance to get rolling!"

    return opportunities


@cached(cache=_MENTOR_OPPS_CACHE, lock=_MENTOR_OPPS_LOCK)
def collect_mentor_panel_opportunities(event_id: str) -> List[Dict]:
    """
    Derive mentor-boost opportunities from the per-team mentor panel state.
    Cached 5 min; short-circuits before any Firestore reads when the event
    is not in its live window (start_date <= now <= end_date + 1 day).
    """
    from datetime import datetime, timedelta
    from common.utils.firebase import get_hackathon_by_event_id
    from db.db import get_db

    opportunities: List[Dict] = []

    try:
        hackathon = get_hackathon_by_event_id(event_id) or {}
    except Exception as e:
        logger.warning("collect_mentor_panel_opportunities: hackathon lookup failed: %s", e)
        hackathon = {}

    now = datetime.now()
    start_date = None
    end_date = None
    try:
        if hackathon.get("start_date"):
            start_date = datetime.fromisoformat(hackathon["start_date"].replace("Z", ""))
        if hackathon.get("end_date"):
            end_date = datetime.fromisoformat(hackathon["end_date"].replace("Z", ""))
    except Exception:
        pass

    is_live = bool(
        start_date and end_date and start_date <= now <= (end_date + timedelta(days=1))
    )

    # Short-circuit before any Firestore reads when outside the live window.
    if not is_live:
        logger.debug("collect_mentor_panel_opportunities: event %s is not live — returning []", event_id)
        return opportunities

    try:
        db = get_db()
        team_docs = db.collection("teams").where("hackathon_event_id", "==", event_id).stream()
    except Exception as e:
        logger.warning("collect_mentor_panel_opportunities: team query failed: %s", e)
        return opportunities

    stale_threshold = now - timedelta(hours=4)

    for snap in team_docs:
        try:
            t = snap.to_dict() or {}
            team_name = t.get("name") or "Unnamed team"
            team_id = snap.id

            flags = t.get("mentor_flags") or []
            open_flags = [f for f in flags if not f.get("resolved_at")][:2]
            for f in open_flags:
                severity = f.get("severity", "needs_attention")
                body = (f.get("body") or "").strip()
                preview = (body[:80] + "...") if len(body) > 80 else body
                opportunities.append({
                    "type": "mentor_opportunity",
                    "team": team_name,
                    "teamPage": f"https://www.ohack.dev/hack/{event_id}/team/{team_id}",
                    "icon": "flag",
                    "value": "Open flag" if severity == "needs_attention" else "Blocked",
                    "description": f"{f.get('raised_by_name', 'Mentor')}: {preview}",
                    "members": len(t.get("users") or []),
                })

            last_touched_iso = t.get("mentor_last_touched_at")
            touched_recently = False
            if last_touched_iso:
                try:
                    last_touched = datetime.fromisoformat(last_touched_iso.replace("Z", ""))
                    if last_touched >= stale_threshold:
                        touched_recently = True
                except Exception:
                    pass
            if not touched_recently:
                opportunities.append({
                    "type": "mentor_opportunity",
                    "team": team_name,
                    "teamPage": f"https://www.ohack.dev/hack/{event_id}/team/{team_id}",
                    "icon": "schedule",
                    "value": "No mentor touch in 4h",
                    "description": "No mentor has checked on this team in the last 4 hours — drop by!",
                    "members": len(t.get("users") or []),
                })
        except Exception as e:
            logger.warning("collect_mentor_panel_opportunities: skipped a team: %s", e)
            continue

    return opportunities

def get_leaderboard_analytics(event_id: str, contributors: List[Dict], achievements: List[Dict], hackathon: Dict = None) -> Dict[str, Any]:
    event_name = None
    github_org = None

    try:
        if hackathon is None:
            hackathon = get_hackathon_by_event_id(event_id)
        if hackathon:
            if "title" in hackathon:
                event_name = hackathon["title"]
            if "github_org" in hackathon:
                github_org = hackathon["github_org"]
    except Exception as e:
        logger.error("Error getting hackathon data for event ID %s: %s", event_id, e)

    general_stats = calculate_general_stats(contributors)
    individual_achievements = categorize_individual_achievements(achievements)
    team_achievements = categorize_team_achievements(achievements, contributors)
    mentor_opportunities = categorize_mentor_opportunities(achievements)

    try:
        mentor_opportunities = mentor_opportunities + collect_mentor_panel_opportunities(event_id)
    except Exception as e:
        logger.warning("Failed to collect mentor panel opportunities for %s: %s", event_id, e)

    return {
        "eventName": event_name,
        "githubOrg": github_org,
        "generalStats": general_stats,
        "individualAchievements": individual_achievements,
        "teamAchievements": team_achievements,
        "mentorOpportunities": mentor_opportunities
    }

def get_github_leaderboard(event_id: str) -> Dict[str, Any]:
    """
    Get GitHub leaderboard data for an event. Cached 5 minutes in Redis.
    """
    # Normalize season-YYYY -> YYYY_season (e.g. "summer-2025" -> "2025_summer")
    normalized_id = _normalize_event_id(event_id)
    if normalized_id != event_id:
        logger.info("get_github_leaderboard: normalized event_id %s -> %s", event_id, normalized_id)
        event_id = normalized_id

    cache_key = f"leaderboard:{event_id}"
    cached_result = get_cached(cache_key)
    if cached_result is not None:
        logger.debug("get_github_leaderboard: cache hit for %s", event_id)
        return cached_result

    start_time = time.time()
    logger.debug("Getting GitHub leaderboard for event ID: %s", event_id)

    # Fetch hackathon ONCE and pass down to all helpers.
    hackathon = get_hackathon_by_event_id(event_id)

    org_data = get_github_organizations(event_id, hackathon=hackathon)

    if not org_data["github_organizations"]:
        logger.info("No GitHub organizations found for event ID: %s", event_id)
        result = {
            "github_organizations": [],
            "github_repositories": [],
            "github_contributors": [],
            "github_achievements": [],
            "generalStats": [],
            "individualAchievements": [],
            "teamAchievements": [],
            "mentorOpportunities": []
        }
        set_cached(cache_key, result, ttl=_LEADERBOARD_CACHE_TTL)
        return result

    repos = get_github_repositories(org_data["github_organizations"][0]["name"])
    contributors_response = get_github_contributors(event_id, hackathon=hackathon)
    contributors = contributors_response.get("github_contributors", [])
    achievements = get_github_achievements(event_id, hackathon=hackathon)
    analytics = get_leaderboard_analytics(event_id, contributors, achievements, hackathon=hackathon)

    total_duration = time.time() - start_time
    logger.debug("Total get_github_leaderboard execution took %.2f seconds", total_duration)

    result = {
        "github_organizations": org_data["github_organizations"],
        "github_repositories": repos["github_repositories"],
        "github_contributors": contributors,
        "github_achievements": achievements,
        **analytics
    }
    set_cached(cache_key, result, ttl=_LEADERBOARD_CACHE_TTL)
    return result
