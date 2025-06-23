import logging
from flask import Blueprint, jsonify, request
from api.github.github_service import (
    get_github_organization_data,
    get_github_repository_data,
    get_github_contributors_by_org,
    get_github_contributors_by_repo
)

logger = logging.getLogger("api.github.github_views")
logger.setLevel(logging.DEBUG)

# Blueprint configuration
BP_NAME = 'api-github'
BP_URL_PREFIX = '/api/github'
bp = Blueprint(BP_NAME, __name__, url_prefix=BP_URL_PREFIX)

@bp.route("/organization/<org_name>", methods=["GET"])
def get_organization_data(org_name):
    """
    Get GitHub organization data including repositories and contributors.

    Args:
        org_name: The GitHub organization name.

    Returns:
        JSON response with GitHub organization data.
    """
    try:
        logger.info("Getting GitHub organization data for: %s", org_name)
        github_data = get_github_organization_data(org_name)

        if "error" in github_data:
            return jsonify(github_data), 404

        return jsonify(github_data)

    except Exception as e:
        logger.error("Error getting GitHub organization data for %s: %s", org_name, str(e))
        return jsonify({"error": str(e)}), 500

@bp.route("/repository", methods=["GET"])
def get_repository_data():
    """
    Get GitHub repository data including contributors.
    
    Query Parameters:
        repo: Required. The repository name.
        org: Optional. The organization name. If not provided, searches across all organizations.

    Returns:
        JSON response with GitHub repository data.
    """
    try:
        repo_name = request.args.get('repo')
        org_name = request.args.get('org')
        
        if not repo_name:
            return jsonify({"error": "repo parameter is required"}), 400
            
        logger.info("Getting GitHub repository data for repo: %s, org: %s", repo_name, org_name)
        github_data = get_github_repository_data(repo_name, org_name)

        if "error" in github_data:
            return jsonify(github_data), 404

        return jsonify(github_data)

    except Exception as e:
        logger.error("Error getting GitHub repository data: %s", str(e))
        return jsonify({"error": str(e)}), 500

@bp.route("/organization/<org_name>/contributors", methods=["GET"])
def get_organization_contributors(org_name):
    """
    Get all contributors for a specific GitHub organization.

    Args:
        org_name: The GitHub organization name.

    Returns:
        JSON response with list of contributors for the organization.
    """
    try:
        logger.info("Getting contributors for organization: %s", org_name)
        contributors = get_github_contributors_by_org(org_name)

        return jsonify({
            "org_name": org_name,
            "contributors": contributors,
            "total_contributors": len(contributors)
        })

    except Exception as e:
        logger.error("Error getting contributors for organization %s: %s",
                    org_name, str(e))
        return jsonify({"error": str(e)}), 500

@bp.route("/repository/contributors", methods=["GET"])
def get_repository_contributors():
    """
    Get all contributors for a specific GitHub repository.
    
    Query Parameters:
        repo: Required. The repository name.
        org: Optional. The organization name. If not provided, searches across all organizations.

    Returns:
        JSON response with list of contributors for the repository.
    """
    try:
        repo_name = request.args.get('repo')
        org_name = request.args.get('org')
        
        if not repo_name:
            return jsonify({"error": "repo parameter is required"}), 400
            
        logger.info("Getting contributors for repo: %s, org: %s", repo_name, org_name)
        contributors = get_github_contributors_by_repo(repo_name, org_name)

        return jsonify({
            "org_name": org_name,
            "repo_name": repo_name,
            "contributors": contributors,
            "total_contributors": len(contributors)
        })

    except Exception as e:
        logger.error("Error getting contributors for repository: %s", str(e))
        return jsonify({"error": str(e)}), 500
