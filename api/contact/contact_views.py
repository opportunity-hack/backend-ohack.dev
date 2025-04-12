from flask import Blueprint, jsonify, request
from common.log import get_logger
from common.exceptions import InvalidInputError
from api.contact.contact_service import submit_contact_form

logger = get_logger(__name__)
bp = Blueprint('contact', __name__, url_prefix='/api')

@bp.route("/contact", methods=["POST"])
def handle_contact_form():
    """
    API endpoint to handle contact form submissions.
    
    Expected JSON payload:
    {
        "firstName": "John",
        "lastName": "Doe",
        "email": "john@example.com",
        "organization": "ABC Corp",
        "inquiryType": "hackathon",
        "message": "I'm interested in participating...",
        "receiveUpdates": true,
        "recaptchaToken": "token-value-here"
    }
    
    Returns:
        JSON response with submission result
    """
    try:
        # Get client IP for rate limiting
        ip_address = request.remote_addr
        
        # Parse request data
        data = request.get_json()
        if not data:
            logger.warning("Empty request body")
            return jsonify({"success": False, "error": "Empty request body"}), 400
        
        # Extract fields with validation
        first_name = data.get('firstName')
        last_name = data.get('lastName')
        email = data.get('email')
        organization = data.get('organization', '')
        inquiry_type = data.get('inquiryType', '')
        message = data.get('message', '')
        receive_updates = data.get('receiveUpdates', False)
        recaptcha_token = data.get('recaptchaToken')        
        
        # Validate required fields
        if not first_name or not last_name or not email or not message:
            logger.warning("Missing required fields in contact form")
            return jsonify({
                "success": False,
                "error": "Missing required fields (firstName, lastName, email, message)"
            }), 400
            
        if not recaptcha_token:
            logger.warning("Missing reCAPTCHA token")
            return jsonify({
                "success": False,
                "error": "Missing reCAPTCHA token"
            }), 400
            
        # Process contact form submission
        result = submit_contact_form(
            ip_address=ip_address,
            first_name=first_name,
            last_name=last_name,
            email=email,
            organization=organization,
            inquiry_type=inquiry_type,
            message=message,
            receive_updates=receive_updates,
            recaptcha_token=recaptcha_token
        )
        
        if result.get('success', False):
            return jsonify(result), 201
        
        return jsonify(result), 400
            
    except InvalidInputError as e:
        logger.warning("Invalid input: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("Error processing contact form: %s", str(e))
        return jsonify({
            "success": False,
            "error": "An error occurred while processing your request"
        }), 500