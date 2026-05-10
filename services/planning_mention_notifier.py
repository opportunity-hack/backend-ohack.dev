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
    """chat.postMessage to a user ID opens (or reuses) a DM channel.

    Bypasses common.utils.slack.send_slack — that helper silently swallows
    SlackApiError, which made every DM look successful even when Slack
    actually rejected it (account_inactive, user_not_found, etc.).
    """
    try:
        from common.utils.slack import get_client
        from slack_sdk.errors import SlackApiError
        from slack_sdk.models.blocks import SectionBlock

        client = get_client()
        try:
            resp = client.chat_postMessage(
                channel=slack_user_id,
                text=message,
                blocks=[SectionBlock(text={"type": "mrkdwn", "text": message})],
                username="Hackathon Bot",
                icon_url="https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Light_Blue.png",
            )
            ts = (resp or {}).get("ts")
            logger.info("Slack DM sent to user %s (ts=%s)", slack_user_id, ts)
            return True, None
        except SlackApiError as e:
            err = (e.response or {}).get("error") or str(e)
            logger.error("Slack DM rejected for user %s: %s", slack_user_id, err)
            return False, err
    except Exception as e:
        logger.exception("Slack DM unexpected error for %s", slack_user_id)
        return False, str(e)


# Resend "from" must be a verified sender. The proven working domain in
# email_service.py is notifs.ohack.org — ohack.dev itself is not verified
# in our Resend account, so welcome@ohack.dev / noreply@ohack.dev get
# rejected silently. Override via MENTION_EMAIL_FROM env var if needed.
DEFAULT_MENTION_FROM = "Opportunity Hack <welcome@notifs.ohack.org>"


def _send_email(to_email, subject, html_body):
    """Best-effort email via Resend (matches services/email_service.py pattern)."""
    try:
        import resend
        api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY") or os.getenv("RESEND_API_KEY")
        if not api_key:
            logger.warning("No Resend API key configured; skipping mention email to %s", to_email)
            return False, "no Resend API key configured"
        resend.api_key = api_key
        from_addr = os.getenv("MENTION_EMAIL_FROM", DEFAULT_MENTION_FROM)
        params = {
            "from": from_addr,
            "to": to_email,
            "subject": subject,
            "html": html_body,
        }
        msg = resend.Emails.SendParams(params)
        result = resend.Emails.send(msg)
        rid = (result or {}).get("id") if isinstance(result, dict) else None
        logger.info("Email sent to %s via Resend (id=%s, from=%s)", to_email, rid, from_addr)
        return True, None
    except Exception as e:
        logger.exception("Email send failed to %s", to_email)
        return False, str(e)


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
    logger.info(
        "Mention dispatch started: actor=%s event=%s card=%s mentions=%d",
        actor_propel_id, hackathon_event_id, card_id, len(mentioned_propel_ids),
    )

    results = {}
    for pid in mentioned_propel_ids:
        if pid == actor_propel_id:
            logger.info("Mention -> %s: skipped (self-mention)", pid)
            results[pid] = "skipped"
            continue

        oauth_user = _get_oauth_user_cached(pid)
        if oauth_user is None:
            logger.warning(
                "Mention -> %s: PropelAuth OAuth lookup returned None — "
                "user may not have a refreshable OAuth token",
                pid,
            )
            results[pid] = "oauth-lookup-failed"
            continue

        slack_id = _extract_slack_user_id(oauth_user)
        email = _extract_email(oauth_user)
        provider = "slack" if slack_id else ("google" if email else "unknown")
        logger.info(
            "Mention -> %s: resolved provider=%s slack_id=%s email=%s",
            pid, provider, slack_id, ("***@" + email.split("@", 1)[1]) if email else None,
        )
        plan_url = f"https://www.ohack.dev/hack/{hackathon_event_id}/plan/c/{card_id}"

        if not slack_id and not email:
            logger.warning(
                "Mention -> %s: no Slack ID or email available; dropping notification",
                pid,
            )
            results[pid] = "no-channel"
            continue

        sent = []
        errors = []

        # Slack DM (when Slack OAuth identity exists). Both Slack-auth and
        # Google-auth users have email; only Slack-auth has a Slack user ID.
        if slack_id:
            preview = MENTION_RE.sub(lambda m: f"@{m.group(1)}", comment_body or "")
            preview = preview[:280] + ("…" if len(preview) > 280 else "")
            msg = (
                f"📋 *{actor_name}* mentioned you in *{card_title}*\n"
                f">{preview}\n"
                f"<{plan_url}|Open card on the planning board>"
            )
            ok, err = _send_slack_dm(slack_id, msg)
            if ok:
                sent.append("slack")
            else:
                errors.append(f"slack:{err}")

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
                f"<p><a href=\"{plan_url}\">Open card on the planning board</a></p>"
                f"<p style=\"color:#888;font-size:12px;\">"
                f"You're getting this because someone mentioned you in a comment on a "
                f"public Opportunity Hack planning board.</p>"
            )
            subject = f"[OHack] {actor_name} mentioned you on the {hackathon_event_id} board"
            ok, err = _send_email(email, subject, html)
            if ok:
                sent.append("email")
            else:
                errors.append(f"email:{err}")

        if sent:
            outcome = "+".join(sent)
            if errors:
                outcome += f" (partial; failures: {', '.join(errors)})"
            results[pid] = outcome
            logger.info("Mention -> %s: %s", pid, outcome)
        else:
            outcome = f"failed ({', '.join(errors)})" if errors else "failed"
            results[pid] = outcome
            logger.error("Mention -> %s: %s", pid, outcome)

    logger.info("Mention dispatch complete: %s", results)
    return results
