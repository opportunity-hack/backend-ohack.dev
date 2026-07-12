import hmac
import os

from flask import Request


def check_api_key(request: Request, *env_var_names: str) -> bool:
    """Validate the X-Api-Key header against the first non-empty env var.

    Used for backend-to-backend (bot) auth where PropelAuth doesn't apply.
    Returns False when no env var is configured so routes fail closed.
    """
    provided = request.headers.get("X-Api-Key")
    if not provided:
        return False
    for name in env_var_names:
        expected = os.getenv(name)
        if expected:
            return hmac.compare_digest(provided, expected)
    return False
