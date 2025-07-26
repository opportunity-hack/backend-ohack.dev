from flask import Blueprint, request, jsonify
from common.auth import auth 
from services import llm_service

bp = Blueprint("llm", __name__, url_prefix="/api/llm")

@bp.route("/summary", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def get_summary():
    """
    Endpoint to generate a summary for a given application.
    Expects application data in the request body.
    """
    application_data = request.get_json()
    if not application_data:
        return jsonify({"error": "Request body cannot be empty."}), 400
    
    summary = llm_service.generate_summary(application_data)
    return jsonify({"summary": summary})

@bp.route("/summary/refresh", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def refresh_summary():
    """
    Endpoint to force a new summary generation for a given application,
    bypassing any cached result.
    """
    application_data = request.get_json()
    if not application_data:
        return jsonify({"error": "Request body cannot be empty."}), 400
    
    # Call the service with force_refresh=True
    summary = llm_service.generate_summary(application_data, force_refresh=True)
    return jsonify({"summary": summary})


@bp.route("/similar-projects", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def get_similar_projects():
    """
    Endpoint to find similar projects for a given application.
    Expects application data in the request body.
    """
    application_data = request.get_json()
    if not application_data:
        return jsonify({"error": "Request body cannot be empty."}), 400

    similar_projects = llm_service.find_similar_projects(application_data)
    return jsonify({"similar_projects": similar_projects})

@bp.route("/similarity-reasoning", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def get_similarity_reasoning():
    """
    Endpoint to generate a reason for similarity between an application and a project.
    Expects { "application": {...}, "project": {...} } in the request body.
    """
    data = request.get_json()
    application_data = data.get('application')
    project_data = data.get('project')

    if not application_data or not project_data:
        return jsonify({"error": "Request must include both 'application' and 'project' data."}), 400

    reasoning = llm_service.generate_similarity_reasoning(application_data, project_data)
    return jsonify({"reasoning": reasoning})

@bp.route("/embedding-map/populate", methods=["POST"])
@auth.require_user # In production, this should ideally be restricted to admin users.
@auth.require_org_member_with_permission("admin_permissions")
def populate_embedding_map_endpoint():
    """
    Triggers a background process to (re)generate embeddings for all NPO
    applications and populate the embedding map collection.
    """
    try:
        result = llm_service.populate_embedding_map()
        return jsonify(result), 200
    except Exception as e:
        # Log the exception e
        return jsonify({"status": "error", "message": "An unexpected error occurred."}), 500