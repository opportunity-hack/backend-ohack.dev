"""Helpers for calling the Resend API without paging Sentry.

Resend rate-limits API calls (HTTP 429). A rate limit means "slow down", not
"something is broken", so we retry with backoff. If we are still limited after
retries, we log at warning level so Sentry stays quiet.
"""
import logging
import time

from resend.exceptions import RateLimitError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def send_with_retry(send_callable, log_context=None):
    """Run a Resend API call, retrying on rate limits with backoff.

    Returns (result, error_message). error_message is None on success, or the
    rate-limit message when every attempt was rate-limited. Any other
    exception propagates immediately -- only rate limits are retried.
    """
    log_context = log_context or {}
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return send_callable(), None
        except RateLimitError as e:
            last_error = e
            if attempt < MAX_ATTEMPTS - 1:
                delay = 2 ** attempt  # 1s, then 2s
                logger.warning(
                    "Resend rate limit hit (attempt %d of %d); retrying in %ds. %s",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    delay,
                    log_context,
                )
                time.sleep(delay)
    logger.warning(
        "Resend rate limit persisted after %d attempts. %s", MAX_ATTEMPTS, log_context
    )
    return None, str(last_error)
