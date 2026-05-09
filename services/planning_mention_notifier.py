"""Send Slack DM or email when someone is @-mentioned in a planning comment.

Mention token format (in markdown body): @[Name](propel_user_id)
The propel_user_id is opaque (no PII). The Name is whatever the author
saw in the picker — we re-derive it server-side from the live profile
when sending the notification.

Notification channel selection:
  - Slack AND email when the user authenticated via Slack (we have both
    a Slack ID and an email on the OAuth profile).
  - Email only when the user authenticated via Google (no Slack ID, but
    Google always returns an email).
  - Drop and log when neither is available. No retries; mentions are
    best-effort.

The dispatcher is called best-effort from create_comment — failures are
logged but never bubble to the API response so a flaky Slack/email
provider can't break the comment write path.
"""
import logging
import os
import re
from cachetools import TTLCache, cached

from common.utils.slack import send_slack

logger = logging.getLogger("planning_mention_notifier")

# Token: @[Name with spaces & punctuation](propel_user_id)
# Name allows anything but `]`, propel_user_id is alphanumeric + dashes/underscores.
MENTION_RE = re.compile(r"@\[([^\]]{1,80})\]\(([A-Za-z0-9_\-|]{1,64})\)")


def parse_mention_ids(text: str):
    """Return a deduped set of propel_user_ids referenced in the text."""
    if not text:
        return set()
    return {m.group(2) for m in MENTION_RE.finditer(text)}


# Cache PropelAuth OAuth lookups for 5 minutes — heavy network call, and
# Slack ID / email don't change in that window.
@cached(cache=TTLCache(maxsize=500, ttl=300))
def _get_oauth_user_cached(propel_id):
    try:
        from services.users_service import get_oauth_user_from_propel_user_id
        return get_oauth_user_from_propel_user_id(propel_id)
    except Exception:
        logger.exception("Failed to fetch OAuth user for %s", propel_id)
        return None


def _extract_slack_user_id(oauth_user):
    """Slack OAuth response includes 'https://slack.com/user_id' (e.g. UC31XTRT5)."""
    if not oauth_user:
        return None
    return oauth_user.get("https://slack.com/user_id") or None


def _extract_email(oauth_user):
    if not oauth_user:
        return None
    return oauth_user.get("email") or None


def _send_slack_dm(slack_user_id, message):
    """chat.postMessage to a user ID opens (or reuses) a DM channel."""
    try:
        send_slack(message=message, channel=slack_user_id)
        return True
    except Exception:
        logger.exception("Slack DM failed for %s", slack_user_id)
        return False


def _send_email(to_email, subject, html_body):
    """Best-effort email via Resend (matches services/email_service.py pattern)."""
    try:
        import resend
        api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY") or os.getenv("RESEND_API_KEY")
        if not api_key:
            logger.warning("No Resend API key configured; skipping mention email to %s", to_email)
            return False
        resend.api_key = api_key
        from_addr = os.getenv("MENTION_EMAIL_FROM", "Opportunity Hack <noreply@ohack.dev>")
        params = {
            "from": from_addr,
            "to": to_email,
            "subject": subject,
            "html": html_body,
        }
        msg = resend.Emails.SendParams(params)
        resend.Emails.send(msg)
        return True
    except Exception:
        logger.exception("Email send failed to %s", to_email)
        return False


def notify_mentions(
    *,
    mentioned_propel_ids,
    actor_propel_id,
    actor_name,
    hackathon_event_id,
    card_title,
    card_id,
    comment_body,
):
    """Best-effort notification. Skips actor self-mentions silently.

    Returns dict {propel_id: "slack" | "email" | "skipped"}.
    """
    results = {}
    for pid in mentioned_propel_ids:
        if pid == actor_propel_id:
            results[pid] = "skipped"
            continue

        oauth_user = _get_oauth_user_cached(pid)
        slack_id = _extract_slack_user_id(oauth_user)
        email = _extract_email(oauth_user)
        plan_url = f"https://www.ohack.dev/hack/{hackathon_event_id}/plan"

        if not slack_id and not email:
            logger.warning("No Slack ID or email for mentioned user %s; dropping notification", pid)
            results[pid] = "no-channel"
            continue

        sent = []

        # Slack DM (when Slack OAuth identity exists). Both Slack-auth and
        # Google-auth users have email; only Slack-auth has a Slack user ID.
        if slack_id:
            preview = MENTION_RE.sub(lambda m: f"@{m.group(1)}", comment_body or "")
            preview = preview[:280] + ("…" if len(preview) > 280 else "")
            msg = (
                f"📋 *{actor_name}* mentioned you in *{card_title}*\n"
                f">{preview}\n"
                f"<{plan_url}|Open the planning board>"
            )
            if _send_slack_dm(slack_id, msg):
                sent.append("slack")

        # Email — sent in addition to Slack so the mention is also durable
        # in the recipient's inbox (Slack-only mentions get buried fast).
        if email:
            preview_html = MENTION_RE.sub(lambda m: f"<strong>@{m.group(1)}</strong>", comment_body or "")
            preview_html = preview_html[:1000]
            html = (
                f"<p><strong>{actor_name}</strong> mentioned you on the "
                f"<em>{hackathon_event_id}</em> planning board, in the card "
                f"<strong>{card_title}</strong>:</p>"
                f"<blockquote style=\"border-left:3px solid #1976d2;padding-left:8px;color:#444;\">"
                f"{preview_html}</blockquote>"
                f"<p><a href=\"{plan_url}\">Open the planning board</a></p>"
                f"<p style=\"color:#888;font-size:12px;\">"
                f"You’re getting this because someone mentioned you in a comment on a "
                f"public Opportunity Hack planning board.</p>"
            )
            subject = f"[OHack] {actor_name} mentioned you on the {hackathon_event_id} board"
            if _send_email(email, subject, html):
                sent.append("email")

        results[pid] = "+".join(sent) if sent else "failed"

    return results
