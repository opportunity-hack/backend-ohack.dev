import logging
from typing import Dict, Any, List
from db.db import get_db
from common.utils.github import get_all_repos

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)

def get_github_organizations(event_id: str) -> Dict[str, Any]:
    """
    Get GitHub organizations for an event.
    
    Args:
        event_id: The event ID to get GitHub organizations for.
        
    Returns:
        Dictionary containing GitHub organizations.
    """
    logger.debug("Getting GitHub organizations for event ID: %s", event_id)
    
    # For now, we're hardcoding the organization name based on the event_id
    # This matches the pattern in common/utils/github.py
    org_name = None
    if event_id == "2024_fall":
        org_name = "2024-Arizona-Opportunity-Hack"
    else:
        logger.error("Unsupported event ID: %s", event_id)
        return {"github_organizations": []}
    
    return {
        "github_organizations": [
            {
                "__id__": org_name,
                "name": org_name
            }
        ]
    }

def get_github_repositories(event_id: str) -> Dict[str, Any]:
    """
    Get GitHub repositories for an event.
    
    Args:
        event_id: The event ID to get GitHub repositories for.
        
    Returns:
        Dictionary containing GitHub repositories.
    """
    logger.debug("Getting GitHub repositories for event ID: %s", event_id)
    
    try:
        # Get all repositories for the event
        repos = get_all_repos(event_id)
        
        github_repos = []
        for repo in repos:
            # Extract repository name
            repo_name = repo["repo_name"]
            github_repos.append({
                "__id__": repo_name,
                "name": repo_name
            })
        
        return {"github_repositories": github_repos}
    except ValueError as e:
        logger.error("Error getting repositories for event ID %s: %s", event_id, e)
        return {"github_repositories": []}

def get_github_contributors(event_id: str) -> Dict[str, Any]:
    """
    Get GitHub contributors for repositories in an event.
    
    Args:
        event_id: The event ID to get GitHub contributors for.
        
    Returns:
        Dictionary containing GitHub contributors.
    """
    logger.debug("Getting GitHub contributors for event ID: %s", event_id)
     # Get organization name
    org_name = None
    if event_id == "2024_fall":
        org_name = "2024-Arizona-Opportunity-Hack"
    else:
        logger.error("Unsupported event ID: %s", event_id)
        return {"github_contributors": []}
    
    try:
        db = get_db()
                
       # Use a collection group query to search across all contributor subcollections
        contributors_ref = db.collection_group('github_contributors').where("org_name", "==", org_name)
    
        docs = contributors_ref.stream()

        contributors = []
        for doc in docs:            
            contribution = doc.to_dict()
            # Add document ID
            contribution["id"] = doc.id
            # Add organization and repository information
            repo_ref = doc.reference.parent.parent
            org_ref = repo_ref.parent.parent
            contribution["repo_name"] = repo_ref.id
            contribution["org_name"] = org_ref.id
            contributors.append(contribution)

            
        return {"github_contributors": contributors}
    
    except Exception as e:
        logger.error("Error getting contributors for event ID %s: %s", event_id, e)
        return {"github_contributors": []}

def calculate_general_stats(contributors: List[Dict]) -> List[Dict]:
    """Calculate general statistics based on contributor data."""
    # Calculate totals from real data
    total_commits = sum(c.get('commits', 0) for c in contributors)
    total_prs = sum(c.get('pull_requests', {}).get('merged', 0) for c in contributors)
    
    # Stub values for data we don't have yet
    return [
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
            "stat": "Hours Spent",
            "value": 1648,  # Stubbed value
            "icon": "accessTime",
            "description": "Collective hours spent coding"
        },
        {
            "stat": "Tasks Completed",
            "value": 136,  # Stubbed value
            "icon": "taskAlt",
            "description": "Tasks completed in project management tools"
        },
        {
            "stat": "Lines of Code", 
            "value": 24892,  # Stubbed value
            "icon": "integrationInstructions",
            "description": "Total lines of code written"
        }
    ]

def calculate_individual_achievements(contributors: List[Dict]) -> List[Dict]:
    """Calculate individual achievements based on contributor data."""
    # Find the contributor with most commits
    most_commits = {"commits": 0}
    for c in contributors:
        if c.get('commits', 0) > most_commits.get('commits', 0):
            most_commits = c
    
    # For real data we have
    individual_achievements = []
    if most_commits.get('commits', 0) > 0:
        individual_achievements.append({
            "title": "Most Commits",
            "person": {
                "name": most_commits.get('login', 'Unknown'),
                "avatar": f"https://avatars.githubusercontent.com/{most_commits.get('login', 'unknown')}",
                "team": "Team " + most_commits.get('repo_name', 'Unknown').split('--')[-1][:10],
                "githubUsername": most_commits.get('login', 'unknown')
            },
            "value": most_commits.get('commits', 0),
            "icon": "code",
            "description": "Most code contributions to the repository",
            "repo": most_commits.get('repo_name', 'Unknown')
        })
    
    # Stub the rest of the data
    stub_achievements = [
        {
            "title": "First to Commit",
            "person": {
                "name": "Jamie Rodriguez",
                "avatar": "https://i.pravatar.cc/150?img=2",
                "team": "Frontend Titans",
                "githubUsername": "jamierodr"
            },
            "value": "00:05:12",
            "icon": "accessTime",
            "description": "First to make a code contribution after kickoff",
            "repo": "frontend-app"
        },
        {
            "title": "Epic PR",
            "person": {
                "name": "Sam Patel",
                "avatar": "https://i.pravatar.cc/150?img=4",
                "team": "Integration Squad",
                "githubUsername": "sampatel"
            },
            "value": "2,548 lines",
            "icon": "merge",
            "description": "Largest pull request merged",
            "repo": "api-integration",
            "prNumber": "42"
        },
        {
            "title": "Night Owl",
            "person": {
                "name": "Jordan Smith",
                "avatar": "https://i.pravatar.cc/150?img=5",
                "team": "Backend Wizards",
                "githubUsername": "jsmith"
            },
            "value": "3:42 AM",
            "icon": "accessTime",
            "description": "Latest commit timestamp",
            "repo": "data-processor",
            "commitId": "a1b2c3d"
        }
    ]
    
    # Add stub data to fill in for any missing real data
    while len(individual_achievements) < 4 and stub_achievements:
        individual_achievements.append(stub_achievements.pop(0))
    
    return individual_achievements

def get_leaderboard_analytics(event_id: str, contributors: List[Dict]) -> Dict[str, Any]:
    """
    Generate leaderboard analytics including statistics and achievements.
    
    Args:
        event_id: The event ID
        contributors: List of contributor data
        
    Returns:
        Dictionary containing leaderboard analytics
    """
    # Set event name and github org based on event_id
    event_name = "Arizona Opportunity Hackathon 2024"
    github_org = "2024-Arizona-Opportunity-Hack"
    
    # Calculate general stats from contributor data
    general_stats = calculate_general_stats(contributors)
    
    # Calculate individual achievements
    individual_achievements = calculate_individual_achievements(contributors)
    
    # For team achievements, we don't have enough data yet so we'll stub it
    team_achievements = [
        {
            "title": "Most Productive Team",
            "team": "Frontend Titans",
            "value": "145 tasks",
            "icon": "group",
            "members": 5,
            "description": "Completed the most tasks during the hackathon",
            "teamPage": "frontend-titans"
        },
        {
            "title": "Most Collaborative",
            "team": "Integration Squad",
            "value": "27 PRs reviewed",
            "icon": "merge",
            "members": 6,
            "description": "Highest number of PR reviews and comments",
            "teamPage": "integration-squad"
        }
    ]
    
    return {
        "eventName": event_name,
        "githubOrg": github_org,
        "generalStats": general_stats,
        "individualAchievements": individual_achievements,
        "teamAchievements": team_achievements
    }

def get_github_leaderboard(event_id: str) -> Dict[str, Any]:
    """
    Get GitHub leaderboard data for an event.
    
    Args:
        event_id: The event ID to get leaderboard data for.
        
    Returns:
        Dictionary containing all GitHub leaderboard data.
    """
    logger.debug("Getting GitHub leaderboard for event ID: %s", event_id)
    
    # Get all data and combine into one response
    orgs = get_github_organizations(event_id)
    repos = get_github_repositories(event_id)
    contributors_response = get_github_contributors(event_id)
    contributors = contributors_response.get("github_contributors", [])
    
    # Calculate analytics for the leaderboard
    analytics = get_leaderboard_analytics(event_id, contributors)
    
    return {
        "github_organizations": orgs["github_organizations"],
        "github_repositories": repos["github_repositories"],
        "github_contributors": contributors,
        **analytics
    }
