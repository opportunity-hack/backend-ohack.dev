import logging
from typing import Dict, Any, Optional, Tuple
from flask import Blueprint, request, jsonify
from common.auth import auth, auth_user
from common.log import get_logger
from common.exceptions import InvalidUsageError
from common.utils.slack import send_slack_audit
from services.volunteers_service import (
    get_volunteer_by_user_id,
    get_volunteers_by_event,
    create_or_update_volunteer,
    update_volunteer_selection,
    get_all_hackers_by_event_id,
    get_mentor_checkin_status,
    mentor_checkin,
    mentor_checkout,
    send_volunteer_message,
)
from common.auth import auth, auth_user

logger = get_logger(__name__)
logger.setLevel(logging.INFO)
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
    
    result = jsonify(response)
    logger.debug(f"Response data: {response}")
    return result, 200

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
    logger.info(f"Submitting {volunteer_type} application for event {event_id}")


    try:
        volunteer_data = _process_request()
        logger.info(f"Received volunteer data: {volunteer_data}")
        # Send Slack Audit message
        send_slack_audit(
            action="submit_volunteer_application",
            message=f"User {user.user_id} submitted a {volunteer_type} application for event {event_id}",
            payload=volunteer_data
        )
        
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
        
        # Get recaptcha token if it exists
        recaptcha_token = volunteer_data.pop('recaptchaToken', None)
        
        # Create or update volunteer record
        result = create_or_update_volunteer(
            user_id=user_id,
            email=email,
            event_id=event_id,
            volunteer_data=volunteer_data,
            created_by=user_id,
            recaptcha_token=recaptcha_token
        )
        
        return _success_response(result, "Application submitted successfully")
    except Exception as e:
        logger.error(f"Error submitting {volunteer_type} application: {str(e)}")
        logger.exception(e)
        return _error_response(f"Failed to submit application: {str(e)}")

def handle_get(user, event_id: str, volunteer_type: str) -> Tuple[Dict[str, Any], int]:
    """Generic handler for retrieving volunteer application."""
    logger.info(f"Getting {volunteer_type} application for event {event_id}")

    try:
        volunteer = get_volunteer_by_user_id(user.user_id, event_id, volunteer_type)
        
        if volunteer:
            result = _success_response(volunteer, "Application retrieved successfully")
            logger.info(f"Retrieved {volunteer_type} application: {result}")            
            return result
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

@bp.route('/volunteer/application_count_by_availability_timeslot/<event_id>', methods=['GET'])
def get_volunteer_application_count_by_timeslot(event_id):
    """Get the count of volunteer applications by availability time slot for a specific event."""
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    try:
        # Get the count of volunteer applications by availability time slot
        from services.volunteers_service import get_volunteer_application_count_by_availability_timeslot
        counts = get_volunteer_application_count_by_availability_timeslot(event_id)
        
        return _success_response(counts, "Volunteer application counts by time slot retrieved successfully")
    except Exception as e:
        logger.error(f"Error retrieving volunteer application counts: {str(e)}")
        # Log stack trace for debugging
        logger.exception(e)
        return _error_response(f"Failed to retrieve application counts: {str(e)}")



@bp.route('/admin/volunteers/<event_id>', methods=['GET'])
@auth.require_org_member_with_permission("all") #TODO
def admin_list_volunteers(user, org, event_id):
    """Admin endpoint to list general volunteer applications."""
    return handle_admin_list(user, event_id, 'volunteer')

# Admin selection update route
@bp.route('/admin/volunteer/<volunteer_id>/select', methods=['POST'])
@auth.require_org_member_with_permission("all") #TODO
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
    
# Generic hacker routes
@bp.route('/hacker/application/<event_id>/submit', methods=['POST'])
@auth.optional_user
def submit_hacker_application(event_id):
    """Submit a hacker application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'hacker')

@bp.route('/hacker/application/<event_id>/update', methods=['POST'])
@auth.optional_user
def update_hacker_application(event_id):
    """Update a hacker application for a specific event."""
    user = auth_user
    
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    return handle_submit(user, event_id, 'hacker')

@bp.route('/hacker/application/<event_id>', methods=['GET'])
@auth.optional_user
def get_hacker_application(event_id):
    """Get a hacker application for a specific event."""
    user = auth_user
    user_id = request.args.get('userId')
    
    if not user and not user_id:
        return _error_response("Authentication or userId required", 401)
    
    try:
        # Use query param userId if provided, otherwise use authenticated user
        effective_user = type('User', (), {'user_id': user_id}) if user_id else user        
        return handle_get(effective_user, event_id, 'hacker')
    except Exception as e:
        logger.error(f"Error retrieving hacker application: {str(e)}")
        return _error_response(f"Failed to retrieve application: {str(e)}")
    
@bp.route('/hacker/applications/<event_id>', methods=['GET'])
@auth.optional_user
def get_hacker_applications(event_id):
    """Get all hacker applications for a specific event. Only if teamStatus is 'I'd like to be matched with a team'. using get_all_hackers_by_event_id"""
    user = auth_user
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    try:
        hackers = get_all_hackers_by_event_id(event_id)
        return _success_response(hackers, "Hacker applications retrieved successfully")
    except Exception as e:
        logger.error(f"Error retrieving hacker applications: {str(e)}")
        return _error_response(f"Failed to retrieve applications: {str(e)}")


# Mentor Check-in API Endpoints
@bp.route('/mentor/checkin/<event_id>/status', methods=['GET'])
@auth.require_user
def get_mentor_checkin_status_endpoint(event_id):
    """Get the current check-in status for the authenticated mentor."""    

    if not event_id:
        return _error_response("Event ID is required", 400)
    
    try:
        # Get the check-in status
        user = auth_user
        status = get_mentor_checkin_status(user.user_id, event_id)
        
        # Return the response
        if status.get('success'):
            return _success_response({
                'isCheckedIn': status.get('isCheckedIn', False),
                'checkInTime': status.get('checkInTime'),
                'timeSlot': status.get('timeSlot')
            }, "Check-in status retrieved successfully")
        else:
            return _error_response(status.get('error', "Failed to retrieve check-in status"), 404)
    except Exception as e:
        logger.error(f"Error retrieving mentor check-in status: {str(e)}")
        return _error_response(f"Failed to retrieve check-in status: {str(e)}")


@bp.route('/mentor/checkin/<event_id>/in', methods=['POST'])
@auth.require_user
def mentor_checkin_endpoint(event_id):
    """Check in a mentor for an event."""
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    try:
        # Get time slot from request if provided
        request_data = request.get_json() or {}
        time_slot = request_data.get('timeSlot')
        user = auth_user
        # Perform check-in
        result = mentor_checkin(user.user_id, event_id, time_slot)
        
        # Return the response
        if result.get('success'):
            return _success_response({
                'message': result.get('message'),
                'checkInTime': result.get('checkInTime'),
                'timeSlot': result.get('timeSlot'),
                'slackNotificationSent': result.get('slackNotificationSent', False)
            }, "Checked in successfully")
        else:
            return _error_response(result.get('error', "Failed to check in"), 400)
    except Exception as e:
        logger.error(f"Error during mentor check-in: {str(e)}")
        return _error_response(f"Failed to check in: {str(e)}")


@bp.route('/mentor/checkin/<event_id>/out', methods=['POST'])
@auth.require_user
def mentor_checkout_endpoint(event_id):
    """Check out a mentor from an event."""
    if not event_id:
        return _error_response("Event ID is required", 400)
    
    try:
        # Perform check-out
        user = auth_user
        result = mentor_checkout(user.user_id, event_id)
        
        # Return the response
        if result.get('success'):
            return _success_response({
                'message': result.get('message'),
                'checkInDuration': result.get('checkInDuration'),
                'slackNotificationSent': result.get('slackNotificationSent', False)
            }, "Checked out successfully")
        else:
            return _error_response(result.get('error', "Failed to check out"), 400)
    except Exception as e:
        logger.error(f"Error during mentor check-out: {str(e)}")
        return _error_response(f"Failed to check out: {str(e)}")


def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")

@bp.route('/admin/<volunteer_id>/message', methods=['POST'])
@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)
def admin_send_volunteer_message(volunteer_id):
    """Admin endpoint to send a message to a volunteer via Slack and email."""
    try:
        # Process request data
        request_data = _process_request()
        message = request_data.get('message')
        recipient_type = request_data.get('recipient_type', 'volunteer')
        recipient_id = request_data.get('recipient_id', volunteer_id)

        if not message:
            return _error_response("Message is required", 400)

        # Use the service function to send the message
        if auth_user and auth_user.user_id: 
            result = send_volunteer_message(
                volunteer_id=volunteer_id, 
                message=message, 
                admin_user_id=auth_user.user_id,
                admin_user=auth_user,
                recipient_type=recipient_type,
                recipient_id=recipient_id
            )

            if result['success']:
                return _success_response(result, "Message sent successfully")

        # Handle different error cases
        if result.get('error') == 'Volunteer not found':
            return _error_response(result['error'], 404)
        if result.get('error') == 'Volunteer email not found':
            return _error_response(result['error'], 400)

        return _error_response(result.get('error', 'Unknown error'), 500)

    except Exception as e:
        logger.error("Error in admin_send_volunteer_message: %s", str(e))
        return _error_response(f"Failed to send message: {str(e)}")
