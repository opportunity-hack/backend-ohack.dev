
"""
Custom exceptions for the OHack application.
"""


class OHackBaseException(Exception):
    """Base exception for all custom exceptions in the OHack application."""
    def __init__(self, message="An error occurred in the OHack application"):
        self.message = message
        super().__init__(self.message)

class ValidationError(OHackBaseException):
    """Exception raised for errors in the input validation."""
    def __init__(self, message="Invalid input provided"):
        super().__init__(message)

class InvalidInputError(ValidationError):
    """Exception raised for invalid input data."""
    def __init__(self, message="Invalid input data provided"):
        super().__init__(message)

class MissingFieldError(ValidationError):
    """Exception raised when a required field is missing."""
    def __init__(self, field_name):
        self.field_name = field_name
        message = f"Required field '{field_name}' is missing"
        super().__init__(message)

class InvalidEmailError(ValidationError):
    """Exception raised for invalid email addresses."""
    def __init__(self, email):
        self.email = email
        message = f"Invalid email address: {email}"
        super().__init__(message)

class InvalidURLError(ValidationError):
    """Exception raised for invalid URLs."""
    def __init__(self, url):
        self.url = url
        message = f"Invalid URL: {url}"
        super().__init__(message)

class DatabaseError(OHackBaseException):
    """Exception raised for errors in database operations."""
    def __init__(self, message="An error occurred during database operation"):
        super().__init__(message)

class DuplicateEntryError(DatabaseError):
    """Exception raised when trying to create a duplicate entry in the database."""
    def __init__(self, entry_type, identifier):
        self.entry_type = entry_type
        self.identifier = identifier
        message = f"{entry_type} with identifier '{identifier}' already exists"
        super().__init__(message)

class NotFoundError(DatabaseError):
    """Exception raised when a requested resource is not found in the database."""
    def __init__(self, resource_type, identifier):
        self.resource_type = resource_type
        self.identifier = identifier
        message = f"{resource_type} with identifier '{identifier}' not found"
        super().__init__(message)

class AuthenticationError(OHackBaseException):
    """Exception raised for errors in authentication."""
    def __init__(self, message="Authentication failed"):
        super().__init__(message)

class AuthorizationError(OHackBaseException):
    """Exception raised for errors in authorization."""
    def __init__(self, message="You are not authorized to perform this action"):
        super().__init__(message)

class RateLimitExceededError(OHackBaseException):
    """Exception raised when API rate limit is exceeded."""
    def __init__(self, message="API rate limit exceeded"):
        super().__init__(message)

class ExternalServiceError(OHackBaseException):
    """Exception raised when an external service (e.g., Slack, GitHub) fails."""
    def __init__(self, service_name, message="External service error"):
        self.service_name = service_name
        message = f"{service_name} service error: {message}"
        super().__init__(message)

# You can add more custom exceptions as needed for your application