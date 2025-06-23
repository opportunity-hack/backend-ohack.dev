import logging
from typing import Dict, Any, List
from db.db import get_db

logger = logging.getLogger("myapp")
logger.setLevel(logging.DEBUG)

def get_github_organization_data(org_name: str) -> Dict[str, Any]:
    """
    Get GitHub organization data including repositories and contributors.

    Args:
        org_name: The GitHub organization name.

    Returns:
        Dictionary containing organization data with repositories and contributors.
    """
    logger.debug("Getting GitHub organization data for: %s", org_name)

    try:
        db = get_db()

        # Get organization document
        org_ref = db.collection('github_organizations').document(org_name)
        org_doc = org_ref.get()

        if not org_doc.exists:
            logger.error("Organization %s not found in database", org_name)
            return {"error": f"Organization {org_name} not found"}

        org_data = org_doc.to_dict()
        org_data["__id__"] = org_name

        # Get repositories for this organization
        repos_ref = org_ref.collection('github_repositories')
        repos_docs = repos_ref.stream()

        repositories = []
        for repo_doc in repos_docs:
            repo_data = repo_doc.to_dict()
            repo_data["__id__"] = repo_doc.id

            # Get contributors for this repository
            contributors_ref = repo_doc.reference.collection('github_contributors')
            contributors_docs = contributors_ref.stream()

            contributors = []
            for contributor_doc in contributors_docs:
                contributor_data = contributor_doc.to_dict()
                contributor_data["id"] = contributor_doc.id
                contributor_data["repo_name"] = repo_doc.id
                contributor_data["org_name"] = org_name
                contributors.append(contributor_data)

            repo_data["contributors"] = contributors
            repositories.append(repo_data)

        org_data["repositories"] = repositories

        return org_data

    except Exception as e:
        logger.error("Error getting GitHub organization data for %s: %s", org_name, e)
        return {"error": str(e)}

def get_github_repository_data(repo_name: str, org_name: str = None) -> Dict[str, Any]:
    """
    Get GitHub repository data including contributors.
    
    Args:
        repo_name: The repository name.
        org_name: Optional organization name. If not provided, searches across all organizations.
        
    Returns:
        Dictionary containing repository data with contributors and search metadata.
    """
    logger.debug("Getting GitHub repository data for repo: %s, org: %s", repo_name, org_name)
    
    # Input validation
    if not repo_name or not repo_name.strip():
        return {"error": "Repository name cannot be empty"}
    
    repo_name = repo_name.strip()
    if org_name:
        org_name = org_name.strip()
    
    try:
        # If org_name is specified, query directly
        if org_name:
            result = _get_repository_with_org(org_name, repo_name)
            if "error" not in result:
                result["search_context"] = {
                    "search_type": "specific_org",
                    "searched_org": org_name,
                    "total_orgs_searched": 1
                }
            return result

        # Search across all organizations for this repository
        result = _search_repository_across_orgs(repo_name)
        return result

    except Exception as e:
        logger.error("Error getting GitHub repository data for %s/%s: %s", org_name or "any", repo_name, e)
        return {"error": f"Internal server error: {str(e)}"}

def _get_repository_with_org(org_name: str, repo_name: str) -> Dict[str, Any]:
    """Get repository data from a specific organization."""
    db = get_db()
    
    # Check if organization exists first
    org_ref = db.collection('github_organizations').document(org_name)
    org_doc = org_ref.get()
    
    if not org_doc.exists:
        return {"error": f"Organization '{org_name}' not found"}
    
    repo_ref = org_ref.collection('github_repositories').document(repo_name)
    repo_doc = repo_ref.get()

    if not repo_doc.exists:
        return {"error": f"Repository '{repo_name}' not found in organization '{org_name}'"}

    repo_data = repo_doc.to_dict()
    repo_data["__id__"] = repo_name
    repo_data["org_name"] = org_name

    # Get contributors for this repository
    contributors_ref = repo_ref.collection('github_contributors')
    contributors_docs = contributors_ref.stream()

    contributors = []
    for contributor_doc in contributors_docs:
        contributor_data = contributor_doc.to_dict()
        contributor_data["id"] = contributor_doc.id
        contributor_data["repo_name"] = repo_name
        contributor_data["org_name"] = org_name
        contributors.append(contributor_data)

    repo_data["contributors"] = contributors
    repo_data["contributor_count"] = len(contributors)
    
    return repo_data

def _search_repository_across_orgs(repo_name: str) -> Dict[str, Any]:
    """Search for repository across all organizations."""
    db = get_db()
    orgs_ref = db.collection('github_organizations')
    orgs_docs = orgs_ref.stream()

    orgs_searched = []
    found_repos = []

    for org_doc in orgs_docs:
        org_name = org_doc.id
        orgs_searched.append(org_name)
        
        repo_ref = org_doc.reference.collection('github_repositories').document(repo_name)
        repo_doc = repo_ref.get()

        if repo_doc.exists:
            repo_data = repo_doc.to_dict()
            repo_data["__id__"] = repo_name
            repo_data["org_name"] = org_name

            # Get contributors for this repository
            contributors_ref = repo_ref.collection('github_contributors')
            contributors_docs = contributors_ref.stream()

            contributors = []
            for contributor_doc in contributors_docs:
                contributor_data = contributor_doc.to_dict()
                contributor_data["id"] = contributor_doc.id
                contributor_data["repo_name"] = repo_name
                contributor_data["org_name"] = org_name
                contributors.append(contributor_data)

            repo_data["contributors"] = contributors
            repo_data["contributor_count"] = len(contributors)
            found_repos.append(repo_data)

    # Handle search results
    if not found_repos:
        return {
            "error": f"Repository '{repo_name}' not found in any organization",
            "search_context": {
                "search_type": "cross_org",
                "searched_orgs": orgs_searched,
                "total_orgs_searched": len(orgs_searched),
                "matches_found": 0
            }
        }
    
    if len(found_repos) == 1:
        # Single match - return the repository data directly
        result = found_repos[0]
        result["search_context"] = {
            "search_type": "cross_org",
            "searched_orgs": orgs_searched,
            "total_orgs_searched": len(orgs_searched),
            "matches_found": 1,
            "found_in_org": result["org_name"]
        }
        return result
    
    # Multiple matches - return all with metadata
    return {
        "multiple_matches": True,
        "matches": found_repos,
        "search_context": {
            "search_type": "cross_org",
            "searched_orgs": orgs_searched,
            "total_orgs_searched": len(orgs_searched),
            "matches_found": len(found_repos),
            "found_in_orgs": [repo["org_name"] for repo in found_repos]
        },
        "message": f"Repository '{repo_name}' found in multiple organizations. Use 'org' parameter to specify which one."
    }

def get_github_contributors_by_org(org_name: str) -> List[Dict]:
    """
    Get all GitHub contributors for an organization.

    Args:
        org_name: The GitHub organization name.

    Returns:
        List of contributor data.
    """
    logger.debug("Getting GitHub contributors for organization: %s", org_name)

    try:
        db = get_db()

        # Use collection group query to get all contributors for this organization
        contributors_ref = db.collection_group('github_contributors')
        contributors_ref = contributors_ref.where("org_name", "==", org_name)
        contributors_docs = contributors_ref.stream()

        contributors = []
        for contributor_doc in contributors_docs:
            contributor_data = contributor_doc.to_dict()
            contributor_data["id"] = contributor_doc.id

            # Add organization and repository information from document path
            repo_ref = contributor_doc.reference.parent.parent
            org_ref = repo_ref.parent.parent
            contributor_data["repo_name"] = repo_ref.id
            contributor_data["org_name"] = org_ref.id

            contributors.append(contributor_data)

        return contributors

    except Exception as e:
        logger.error("Error getting GitHub contributors for organization %s: %s",
                    org_name, e)
        return []

def get_github_contributors_by_repo(repo_name: str, org_name: str = None) -> List[Dict]:
    """
    Get all GitHub contributors for a repository.

    Args:
        repo_name: The repository name.
        org_name: Optional organization name. If not provided, searches across all organizations.

    Returns:
        List of contributor data.
    """
    logger.debug("Getting GitHub contributors for repo: %s, org: %s", repo_name, org_name)

    try:
        db = get_db()

        contributors_ref = db.collection_group('github_contributors')
        contributors_ref = contributors_ref.where("repo_name", "==", repo_name)
        
        if org_name:
            contributors_ref = contributors_ref.where("org_name", "==", org_name)

        contributors_docs = contributors_ref.stream()

        contributors = []
        for contributor_doc in contributors_docs:
            contributor_data = contributor_doc.to_dict()
            contributor_data["id"] = contributor_doc.id

            # Add organization and repository information from document path
            repo_ref = contributor_doc.reference.parent.parent
            org_ref = repo_ref.parent.parent
            contributor_data["repo_name"] = repo_ref.id
            contributor_data["org_name"] = org_ref.id

            contributors.append(contributor_data)

        return contributors

    except Exception as e:
        logger.error("Error getting GitHub contributors for repository %s/%s: %s",
                    org_name or "any", repo_name, e)
        return []