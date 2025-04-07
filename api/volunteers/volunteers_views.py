from flask import Blueprint, request, jsonify
import json
from typing import Dict, Any, Optional, List, Tuple
from common.auth import auth, auth_user
from common.log import get_logger
from common.exceptions import InvalidUsageError
from services.volunteers_service import (
    get_volunteer_by_user_id,
    get_volunteers_by_event,
    create_or_update_volunteer,
    update_volunteer_selection
)

logger = get_logger(__name__)
bp = Blueprint('volunteers', __name__, url_prefix='/api')

# Helper functions
def _process_request() -> Dict[str, Any]:
    """Process request data and return JSON dictionary."""
    request_data = request.get_json()
    if not request_data:
        raise InvalidUsageError("Missing request body", status_code=400)
    return request_data

def _get_pagination_params() -> Tuple[int, int, Optional[bool]]:
    """Extract pagination parameters from request."""
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    selected = request.args.get('selected')
    
    if selected is not None:
        selected = selected.lower() == 'true'
    
    return page, limit, selected

def _success_response(data: Dict[str, Any] = None, message: str = "Success") -> Tuple[Dict[str, Any], int]:
    """Generate a success response."""
    response = {
        "success": True,
        "message": message
    }
    
    if data:
        response["data"] = data
    
    return jsonify(response), 200

def _error_response(message: str, status_code: int = 400) -> Tuple[Dict[str, Any], int]:
    """Generate an error response."""
    return jsonify({
        "success": False,
        "message": message,
        "status_code": status_code
    }), status_code

# Generic route handlers
def handle_submit(user, event_id: str, volunteer_type: str) -> Tuple[Dict[str, Any], int]:
    """Generic handler for volunteer application submission."""
    try:
        volunteer_data = _process_request()
        
        # Ensure volunteer_type is set correctly
        volunteer_data['volunteer_type'] = volunteer_type

        # Get email from volunteer_data
        email = volunteer_data.get('email')
        if not email:
            return _error_response("Email is required", 400)
        # Validate email format
        if not isinstance(email, str) or '@' not in email:
            return _error_response("Invalid email format", 400)
        
        # Set user_id if provided in user, otherwise see if it's in volunteer_data, otherwise use None
        user_id = None
        if hasattr(user, 'user_id'):
            user_id = user.user_id
        elif 'user_id' in volunteer_data:
            user_id = volunteer_data['user_id']
        
        # Set appropriate type field based on volunteer_type
        type_mapping = {
            'mentor': 'mentors',
            'sponsor': 'sponsors',
            'judge': 'judges',
            'hacker': 'hackers',
            'volunteer': 'volunteers'
        }
        volunteer_data['type'] = type_mapping.get(volunteer_type, volunteer_type)
        
        # Set event_id
        volunteer_data['event_id'] = event_id
        
        # Create or update volunteer record
        result = create_or_update_volunteer(
            user_id=user_id,
            email=email,
            event_id=event_id,
            volunteer_data=volunteer_data,
            created_by=user_id
        )
        
        return _success_response(result, "Application submitted successfully")
    except Exception as e:
        logger.error(f"Error submitting {volunteer_type} application: {str(e)}")
        logger.exception(e)
        return _error_response(f"Failed to submit application: {str(e)}")

def handle_get(user, event_id: str, volunteer_type: str) -> Tuple[Dict[str, Any], int]:
    """Generic handler for retrieving volunteer application."""
    try:
        volunteer = get_volunteer_by_user_id(user.user_id, event_id, volunteer_type)
        
        if volunteer:
            return _success_response(volunteer, "Application retrieved successfully")
        else:
            return _success_response(None, "No application found")
    except Exception as e:
        logger.error(f"Error retrieving {volunteer_type} application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")

def handle_admin_list(user, event_id: str, volunteer_type: str) -> Tuple[Dict[str, Any], int]:
    """Generic handler for admin listing of volunteer applications."""
    try:
        page, limit, selected = _get_pagination_params()
        volunteers = get_volunteers_by_event(event_id, volunteer_type, page, limit, selected)
        
        return _success_response({
            "volunteers": volunteers,
            "page": page,
            "limit": limit,
            "total": len(volunteers)  # This is not accurate for total count but works for current page
        }, f"{volunteer_type.capitalize()} applications retrieved successfully")
    except Exception as e:
        logger.error(f"Error listing {volunteer_type} applications: {str(e)}")
        return _error_response(f"Failed to list applications: {str(e)}")

# Mentor routes
@bp.route('/mentor/application/<event_id>/submit', methods=['POST'])
@auth.optional_user
def submit_mentor_application(event_id):    
    """Submit a mentor application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'mentor')

@bp.route('/mentor/application/<event_id>/update', methods=['POST'])
@auth.optional_user
def update_mentor_application(event_id):
    """Update a mentor application for a specific event."""
    user = auth_user
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'mentor')

@bp.route('/mentor/application/<event_id>', methods=['GET'])
@auth.optional_user
def get_mentor_application(event_id):
    """Get a mentor application for a specific event."""
    user = auth_user
    user_id = request.args.get('userId')
    
    if not user and not user_id:
        return _error_response("Authentication or userId required", 401)
    
    try:
        # Use query param userId if provided, otherwise use authenticated user
        effective_user = type('User', (), {'user_id': user_id}) if user_id else user
        return handle_get(effective_user, event_id, 'mentor')
    except Exception as e:
        logger.error(f"Error retrieving mentor application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")

@bp.route('/admin/mentors/<event_id>', methods=['GET'])
@auth.require_org_member_with_permission("all")
def admin_list_mentors(user, org, event_id):
    """Admin endpoint to list mentor applications."""
    return handle_admin_list(user, event_id, 'mentor')

# Sponsor routes
@bp.route('/sponsor/application/<event_id>/submit', methods=['POST'])
@auth.optional_user
def submit_sponsor_application(event_id):
    """Submit a sponsor application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'sponsor')

@bp.route('/sponsor/application/<event_id>/update', methods=['POST'])
@auth.optional_user
def update_sponsor_application(event_id):
    """Update a sponsor application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'sponsor')

@bp.route('/sponsor/application/<event_id>', methods=['GET'])
@auth.optional_user
def get_sponsor_application(event_id):
    """Get a sponsor application for a specific event."""
    user = auth_user
    user_id = request.args.get('userId')
    
    if not user and not user_id:
        return _error_response("Authentication or userId required", 401)
    
    try:
        # Use query param userId if provided, otherwise use authenticated user
        effective_user = type('User', (), {'user_id': user_id}) if user_id else user
        return handle_get(effective_user, event_id, 'sponsor')
    except Exception as e:
        logger.error(f"Error retrieving sponsor application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")

@bp.route('/admin/sponsors/<event_id>', methods=['GET'])
@auth.require_org_member_with_permission("all")
def admin_list_sponsors(user, org, event_id):
    """Admin endpoint to list sponsor applications."""
    return handle_admin_list(user, event_id, 'sponsor')

# Judge routes
@bp.route('/judge/application/<event_id>/submit', methods=['POST'])
@auth.optional_user
def submit_judge_application(event_id):
    """Submit a judge application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'judge')

@bp.route('/judge/application/<event_id>/update', methods=['POST'])
@auth.optional_user
def update_judge_application(event_id):
    """Update a judge application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'judge')

@bp.route('/judge/application/<event_id>', methods=['GET'])
@auth.optional_user
def get_judge_application(event_id):
    """Get a judge application for a specific event."""
    user = auth_user
    user_id = request.args.get('userId')
    
    if not user and not user_id:
        return _error_response("Authentication or userId required", 401)
    
    try:
        # Use query param userId if provided, otherwise use authenticated user
        effective_user = type('User', (), {'user_id': user_id}) if user_id else user
        return handle_get(effective_user, event_id, 'judge')
    except Exception as e:
        logger.error(f"Error retrieving judge application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")

@bp.route('/admin/judges/<event_id>', methods=['GET'])
@auth.require_org_member_with_permission("all")
def admin_list_judges(user, org, event_id):
    """Admin endpoint to list judge applications."""
    return handle_admin_list(user, event_id, 'judge')

# Generic volunteer routes
@bp.route('/volunteer/application/<event_id>/submit', methods=['POST'])
@auth.optional_user
def submit_volunteer_application(event_id):
    """Submit a general volunteer application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'volunteer')

@bp.route('/volunteer/application/<event_id>/update', methods=['POST'])
@auth.optional_user
def update_volunteer_application(event_id):
    """Update a general volunteer application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'volunteer')

@bp.route('/volunteer/application/<event_id>', methods=['GET'])
@auth.optional_user
def get_volunteer_application(event_id):
    """Get a general volunteer application for a specific event."""
    user = auth_user
    user_id = request.args.get('userId')
    
    if not user and not user_id:
        return _error_response("Authentication or userId required", 401)
    
    try:
        # Use query param userId if provided, otherwise use authenticated user
        effective_user = type('User', (), {'user_id': user_id}) if user_id else user
        return handle_get(effective_user, event_id, 'volunteer')
    except Exception as e:
        logger.error(f"Error retrieving volunteer application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")

@bp.route('/admin/volunteers/<event_id>', methods=['GET'])
@auth.require_org_member_with_permission("all")
def admin_list_volunteers(user, org, event_id):
    """Admin endpoint to list general volunteer applications."""
    return handle_admin_list(user, event_id, 'volunteer')

# Admin selection update route
@bp.route('/admin/volunteer/<volunteer_id>/select', methods=['POST'])
@auth.require_org_member_with_permission("all")
def admin_update_selection(user, org, volunteer_id):
    """Admin endpoint to update volunteer selection status."""
    try:
        data = _process_request()
        selected = data.get('selected', False)
        
        updated_volunteer = update_volunteer_selection(
            volunteer_id=volunteer_id,
            selected=selected,
            updated_by=user.user_id
        )
        
        if updated_volunteer:
            return _success_response(updated_volunteer, "Selection status updated successfully")
        else:
            return _error_response("Volunteer not found", 404)
    except Exception as e:
        logger.error(f"Error updating volunteer selection: {str(e)}")
        return _error_response(f"Failed to update selection status: {str(e)}")