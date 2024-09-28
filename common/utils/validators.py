import re
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

# Regular expression for email validation
# This regex follows the RFC 5322 standard for email addresses
EMAIL_REGEX = re.compile(r"""(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])""", re.IGNORECASE)

def validate_email(email):
    """
    Validate an email address.

    Args:
    email (str): The email address to validate.

    Returns:
    bool: True if the email is valid, False otherwise.
    """
    if not isinstance(email, str):
        logger.warning(f"Invalid email type: {type(email)}")
        return False
    
    if not email or len(email) > 254:
        return False

    try:
        if re.match(EMAIL_REGEX, email):
            return True
    except re.error:
        logger.exception("Regex error in email validation")
    
    return False

def validate_url(url):
    """
    Validate a URL.

    Args:
    url (str): The URL to validate.

    Returns:
    bool: True if the URL is valid, False otherwise.
    """
    if not isinstance(url, str):
        logger.warning(f"Invalid URL type: {type(url)}")
        return False

    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        logger.warning(f"Invalid URL format: {url}")
        return False

def sanitize_string(input_string, max_length=None):
    """
    Sanitize a string by trimming whitespace and optionally truncating.

    Args:
    input_string (str): The string to sanitize.
    max_length (int, optional): The maximum length of the string. If provided, 
                                the string will be truncated to this length.

    Returns:
    str: The sanitized string.
    """
    if not isinstance(input_string, str):
        logger.warning(f"Invalid input type for sanitization: {type(input_string)}")
        return ""

    sanitized = input_string.strip()
    
    if max_length is not None and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logger.info(f"String truncated to {max_length} characters")

    return sanitized

# You can add more validator functions as needed

if __name__ == "__main__":
    # Simple tests
    print(validate_email("test@example.com"))  # Should print True
    print(validate_email("invalid-email"))  # Should print False
    print(validate_url("https://www.example.com"))  # Should print True
    print(validate_url("invalid-url"))  # Should print False
    print(sanitize_string("  Hello, World!  "))  # Should print "Hello, World!"
    print(sanitize_string("Too long", 5))  # Should print "Too l"