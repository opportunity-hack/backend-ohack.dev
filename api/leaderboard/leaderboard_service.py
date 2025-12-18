import logging
from typing import Dict, Any, List
from db.db import get_db
from common.utils.github import get_all_repos
from common.utils.firebase import get_hackathon_by_event_id
import time

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
    
    try:
        # Get the hackathon document
        hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.error("Hackathon not found for event ID: %s", event_id)
            return {"github_organizations": []}
        
        # Get the github_org from the hackathon document
        org_name = hackathon.get("github_org")
        if not org_name:
            logger.error("GitHub organization not found in hackathon data for event ID: %s", event_id)    
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
    """
    Get GitHub repositories for an event.
    
    Args:
        event_id: The event ID to get GitHub repositories for.
        
    Returns:
        Dictionary containing GitHub repositories.
    """
    logger.debug("Getting GitHub repositories for org_name: %s", org_name)

    # Use the database to get all repositories for the organization
    if not org_name:
        logger.error("Organization name is empty or None")
        return {"github_repositories": []}
    
    # Given the org_name, get the child repositories as part of the group    
    organization_ref = get_db().collection('github_organizations').document(org_name)    
    repos_ref = organization_ref.collection('github_repositories')
    logger.debug("Using organization reference: %s", organization_ref.id)

    try:
        # Check if the organization exists
        if not organization_ref.get().exists:
            logger.error("Organization %s not found in database", org_name)
            return {"github_repositories": []}
        
        # Get all repositories for the organization
        repos = repos_ref.stream()
        
        if not repos:
            logger.warning("No repositories found for organization %s", org_name)
            return {"github_repositories": []}
        
        github_repositories = []
        for repo in repos:
            repo_data = repo.to_dict()
            # Add document ID if not present
            if "__id__" not in repo_data:
                repo_data["__id__"] = repo.id
            # Add organization name
            repo_data["org_name"] = org_name
            github_repositories.append(repo_data)
        
        return {"github_repositories": github_repositories}
        
    except Exception as e:
        logger.error("Error getting repositories for organization %s: %s", org_name, e)
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
    
    try:
        # Get the hackathon document
        hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.error("Hackathon not found for event ID: %s", event_id)
            return {"github_contributors": []}
        
        # Get the github_org from the hackathon document
        org_name = hackathon.get("github_org")
        if not org_name:
            logger.error("GitHub organization not found in hackathon data for event ID: %s", event_id)            
            return {"github_contributors": []}
        
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

def get_github_achievements(event_id: str) -> List[Dict]:
    """
    Get GitHub achievements from the achievements collection group.
    
    Args:
        event_id: The event ID to get achievements for.
        
    Returns:
        List of achievement objects.
    """
    logger.debug("Getting GitHub achievements for event ID: %s", event_id)
    
    try:
        # Get the hackathon document
        hackathon = get_hackathon_by_event_id(event_id)
        if not hackathon:
            logger.error("Hackathon not found for event ID: %s", event_id)
            return []
        
        # Get the github_org from the hackathon document
        org_name = hackathon.get("github_org")
        if not org_name:
            logger.error("GitHub organization not found in hackathon data for event ID: %s", event_id)                    
            return []
        
        db = get_db()

        # Use the github_organizations collection, then get the achievements collection group
        organization_ref = db.collection('github_organizations').document(org_name)
        if not organization_ref:
            logger.error("Organization %s not found in database", org_name)
            return []
        # Get the organization name from the document
        org_name = organization_ref.id
        # Log the organization name being used
        logger.debug("Using organization name: %s", org_name)
        # Get the achievements collection group
        # Check if the organization has a subcollection named 'achievements'
        if not organization_ref.collection('achievements').get():
            logger.error("No achievements found for organization %s", org_name)
            return []
        # Log the organization ID being used
        logger.debug("Using organization ID: %s", organization_ref.id)
        # Log the achievements collection being used
        logger.debug("Using achievements collection for organization %s", org_name)
        # Get the achievements collection group
        achievements_ref = organization_ref.collection('achievements')
        
        
        docs = achievements_ref.stream()
        
        achievements = []
        for doc in docs:
            achievement = doc.to_dict()
            # Add document ID if not present
            if "__id__" not in achievement:
                achievement["__id__"] = doc.id
            achievements.append(achievement)
            
        logger.debug("Found %d achievements for organization %s", len(achievements), org_name)
        
        # Log the first few achievements to help with debugging
        if achievements:
            logger.debug("First achievement example: %s", str(achievements[0]))
        else:
            logger.warning("No achievements found for organization %s", org_name)
        
        # Sort achievements by their timestamp if available
        achievements.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        return achievements
    
    except Exception as e:
        logger.error("Error getting achievements for event ID %s: %s", event_id, e, exc_info=True)
        return []

def calculate_general_stats(contributors: List[Dict]) -> List[Dict]:
    """Calculate general statistics based on contributor data."""
    # Calculate totals from real data
    total_commits = sum(c.get('commits', 0) for c in contributors)
    total_prs = sum(c.get('pull_requests', {}).get('merged', 0) for c in contributors)
    total_issues_closed = sum(c.get('issues', {}).get('closed', 0) for c in contributors)
    
    # Calculate lines of code if available
    total_additions = sum(c.get('additions', 0) for c in contributors)
    total_deletions = sum(c.get('deletions', 0) for c in contributors)
    total_lines = total_additions - total_deletions if total_additions > 0 or total_deletions > 0 else 0
    
    # Count unique contributors
    unique_contributors = set()
    for c in contributors:
        if c.get('login'):
            unique_contributors.add(c.get('login'))
    
    # Create a list for stats with real data
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
    
    # Add lines of code if we have that data
    if total_lines > 0:
        stats.append({
            "stat": "Lines of Code", 
            "value": total_lines,
            "icon": "integration_instructions",
            "description": "Net lines of code added"
        })
    
    return stats

def categorize_individual_achievements(achievements: List[Dict]) -> List[Dict]:
    """
    Categorize and format individual achievements.
    
    Args:
        achievements: List of achievement objects
        
    Returns:
        List of formatted individual achievements
    """
    logger.debug("Starting categorize_individual_achievements with %d total achievements", len(achievements))
    
    # Filter for individual achievements (those with a person field)
    individual_achievements = [a for a in achievements if "person" in a]
    logger.debug("Found %d individual achievements with 'person' field", len(individual_achievements))
    
    if not individual_achievements:
        logger.warning("No individual achievements found. Sample of first achievements: %s", 
                      str(achievements[:3]) if achievements else "[]")
        return []
    
    # Log a sample of achievements for debugging
    logger.debug("Sample of first individual achievement: %s", 
                str(individual_achievements[0]) if individual_achievements else "None")
    
    # Ensure each achievement has all required fields
    for i, achievement in enumerate(individual_achievements):
        # Log the achievement ID being processed
        logger.debug("Processing achievement %d: %s", i, achievement.get("__id__", "unknown_id"))
        
        # Set default values for any missing fields
        if "icon" not in achievement:
            achievement["icon"] = "star"
            logger.debug("Added default icon 'star' to achievement %s", achievement.get("__id__", "unknown_id"))
            
        if "value" not in achievement:
            achievement["value"] = "-"
            logger.debug("Added default value '-' to achievement %s", achievement.get("__id__", "unknown_id"))
            
        if "description" not in achievement:
            achievement["description"] = achievement.get("title", "Achievement")
            logger.debug("Added default description to achievement %s", achievement.get("__id__", "unknown_id"))
            
        # Ensure person field has all required subfields
        if "person" in achievement:
            person = achievement["person"]
            logger.debug("Person data for achievement %s: %s", 
                        achievement.get("__id__", "unknown_id"), str(person))
            
            if "name" not in person:
                person["name"] = person.get("githubUsername", "Unknown")
                logger.debug("Added default name from githubUsername: %s", person["name"])
                
            if "avatar" not in person:
                person["avatar"] = f"https://avatars.githubusercontent.com/{person.get('githubUsername', 'unknown')}"
                logger.debug("Added default avatar URL for user: %s", person.get("githubUsername", "unknown"))
                
            if "team" not in person or not person["team"]:
                repo_name = achievement.get("repo", "")
                if "--" in repo_name:
                    person["team"] = "Team " + repo_name.split("--")[-1][:15]
                else:
                    person["team"] = repo_name
                logger.debug("Added derived team name: %s from repo: %s", 
                            person["team"], achievement.get("repo", ""))
    
    # Sort achievements to highlight the most interesting ones first
    priority_titles = ["Most Commits", "Epic PR", "First to Commit", "Night Owl"]
    
    try:
        sorted_achievements = sorted(
            individual_achievements, 
            key=lambda x: (
                priority_titles.index(x.get("title", "")) if x.get("title", "") in priority_titles else 999,
                x.get("timestamp", "")
            )
        )
        logger.debug("Successfully sorted %d achievements", len(sorted_achievements))
    except Exception as e:
        logger.error("Error sorting achievements: %s", e)
        logger.error("Achievement titles: %s", [a.get("title") for a in individual_achievements])
        # Fall back to unsorted if there's an error
        sorted_achievements = individual_achievements
    
    # Limit to top achievements
    result = sorted_achievements[:8]
    logger.debug("Returning %d individual achievements", len(result))
    
    # Log the titles of achievements being returned
    logger.debug("Achievement titles being returned: %s", 
                [a.get("title") for a in result])
    
    return result

def categorize_team_achievements(achievements: List[Dict], contributors: List[Dict]) -> List[Dict]:
    """
    Generate team achievements based on available data.
    
    Args:
        achievements: List of achievement objects
        contributors: List of contributor data
        
    Returns:
        List of team achievements
    """
    # Check if there are any team achievements in the achievements list
    team_achievements = [a for a in achievements if "team" in a and "person" not in a]
    
    # If we have team achievements, use them
    if team_achievements:
        # Ensure each achievement has all required fields
        for achievement in team_achievements:
            if "icon" not in achievement:
                achievement["icon"] = "group"
            if "value" not in achievement:
                achievement["value"] = "-"
            if "description" not in achievement:
                achievement["description"] = achievement.get("title", "Team Achievement")
        
        # Return top team achievements (limit to 4)
        return team_achievements[:4]
    
    # Otherwise, calculate team achievements from contributor data
    # Group contributors by repository
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
            
        # Add contributor metrics to team totals
        teams_data[repo_name]['commits'] += contributor.get('commits', 0)
        teams_data[repo_name]['prs_merged'] += contributor.get('pull_requests', {}).get('merged', 0)
        teams_data[repo_name]['contributors'].add(contributor.get('login'))
    
    # Convert teams_data to a list for sorting
    teams_list = []
    for repo, data in teams_data.items():
        teams_list.append({
            'repo_name': repo,
            'team_name': data['team_name'],
            'commits': data['commits'],
            'prs_merged': data['prs_merged'],
            'contributor_count': len(data['contributors'])
        })
    
    # Sort teams by different metrics to find the achievements
    most_productive = sorted(teams_list, key=lambda x: x['commits'], reverse=True)
    most_collaborative = sorted(teams_list, key=lambda x: x['prs_merged'], reverse=True)
    largest_team = sorted(teams_list, key=lambda x: x['contributor_count'], reverse=True)
    
    calculated_team_achievements = []
    
    # Add most productive team (most commits)
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
    
    # Add most collaborative team (most PRs)
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
    
    # Add largest team (most contributors)
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

def get_leaderboard_analytics(event_id: str, contributors: List[Dict], achievements: List[Dict]) -> Dict[str, Any]:
    """
    Generate leaderboard analytics including statistics and achievements.
    
    Args:
        event_id: The event ID
        contributors: List of contributor data
        achievements: List of achievement data
        
    Returns:
        Dictionary containing leaderboard analytics
    """
    # Default values in case we can't find the data
    event_name = None
    github_org = None
    
    try:
        # Get the hackathon document
        hackathon = get_hackathon_by_event_id(event_id)
        if hackathon:
            # Get the event name from the hackathon title
            if "title" in hackathon:
                event_name = hackathon["title"]
            
            # Get the github_org from the hackathon document
            if "github_org" in hackathon:
                github_org = hackathon["github_org"]
    except Exception as e:
        logger.error("Error getting hackathon data for event ID %s: %s", event_id, e)
    
    # Calculate general stats from contributor data
    general_stats = calculate_general_stats(contributors)
    
    # Process achievements
    individual_achievements = categorize_individual_achievements(achievements)
    team_achievements = categorize_team_achievements(achievements, contributors)
    
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
    start_time = time.time()
    logger.debug("Getting GitHub leaderboard for event ID: %s", event_id)
    
    # Get organization and repository data
    org_start = time.time()
    org_name = get_github_organizations(event_id)
    org_duration = time.time() - org_start
    logger.debug("get_github_organizations took %.2f seconds", org_duration)

    if org_name["github_organizations"] == []:
        logger.warning("No GitHub organizations found for event ID: %s", event_id)
        return {
            "github_organizations": [],
            "github_repositories": [],
            "github_contributors": [],
            "github_achievements": [],
            "generalStats": [],
            "individualAchievements": [],
            "teamAchievements": []
        }
    
    repos_start = time.time()
    repos = get_github_repositories(org_name["github_organizations"][0]["name"])
    repos_duration = time.time() - repos_start
    logger.debug("get_github_repositories took %.2f seconds", repos_duration)
    
    # Get contributor data
    contributors_start = time.time()
    contributors_response = get_github_contributors(event_id)
    contributors = contributors_response.get("github_contributors", [])
    contributors_duration = time.time() - contributors_start
    logger.debug("get_github_contributors took %.2f seconds and found %d contributors", 
                contributors_duration, len(contributors))
    
    # Get achievement data from the new achievements collection
    achievements_start = time.time()
    achievements = get_github_achievements(event_id)
    achievements_duration = time.time() - achievements_start
    logger.debug("get_github_achievements took %.2f seconds and found %d achievements", 
                achievements_duration, len(achievements))
    
    # Calculate analytics for the leaderboard
    analytics_start = time.time()
    analytics = get_leaderboard_analytics(event_id, contributors, achievements)
    analytics_duration = time.time() - analytics_start
    logger.debug("get_leaderboard_analytics took %.2f seconds", analytics_duration)
    
    # Log the count of achievements in the final result for debugging
    individual_count = len(analytics.get("individualAchievements", []))
    team_count = len(analytics.get("teamAchievements", []))
    logger.debug("Final counts - Individual achievements: %d, Team achievements: %d", 
                individual_count, team_count)
    
    total_duration = time.time() - start_time
    logger.debug("Total get_github_leaderboard execution took %.2f seconds", total_duration)
    
    return {
        "github_organizations": org_name["github_organizations"],
        "github_repositories": repos["github_repositories"],
        "github_contributors": contributors,
        "github_achievements": achievements,  # Include the full achievements list
        **analytics
    }
