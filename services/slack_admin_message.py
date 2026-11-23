"""Admin-composed Slack messages (the /admin/communication Slack tab).

Unlike common.utils.slack.send_slack, which swallows every Slack error, a
send here reports what happened: the channel, the timestamp and a permalink,
or a named refusal the admin can act on.

Lives outside api/slack so it can be imported (and tested in CI) without
api/slack/__init__ pulling in common.auth, which needs a real PropelAuth URL.
"""
import re
from typing import Any, Dict, List, Optional

from slack_sdk.errors import SlackApiError

from common.log import get_logger
from common.utils import slack as slack_utils

logger = get_logger(__name__)

# Slack: a section block's text holds 3000 characters and a message 50
# blocks. The message cap here is far below that on purpose.
MAX_SECTION_CHARS = 3000
MAX_MESSAGE_CHARS = 12000
MAX_CHANNELS_LISTED = 5000
BOT_NAME = "Hackathon Bot"
BOT_ICON_URL = "https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Light_Blue.png"
AUDIT_PREVIEW_CHARS = 300

_CONVERSATION_ID_RE = re.compile(r"^[CGD][A-Z0-9]{8,}$")
_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")
_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

# Slack error code -> (HTTP status, what the admin should do about it).
_SLACK_ERRORS = {
    "not_in_channel": (409, "The bot is not in that channel. In Slack, open the "
                            "channel and run /invite @Hackathon Bot, then send again."),
    "channel_not_found": (404, "Slack could not find that channel. Private channels "
                               "only work after the bot has been invited."),
    "is_archived": (409, "That channel is archived."),
    "msg_too_long": (400, "Slack says the message is too long. Shorten it and send again."),
    "restricted_action": (403, "Workspace settings stop the bot posting in that channel."),
    "ratelimited": (429, "Slack is rate limiting the bot. Wait a minute and send again."),
}


class SlackMessageError(Exception):
    """A refusal with a stable code, a sentence for the admin and an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def split_into_sections(message: str, limit: int = MAX_SECTION_CHARS) -> List[str]:
    """Split a message into chunks of at most ``limit`` characters.

    A message that fits is returned whole, so its spacing is exactly what the
    admin typed. A longer one is cut at blank lines first, then at line
    breaks, then mid-line as a last resort.
    """
    if len(message) <= limit:
        return [message]

    pieces: List[str] = []
    for paragraph in re.split(r"\n\s*\n", message):
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        line_chunk = ""
        for line in paragraph.split("\n"):
            while len(line) > limit:
                if line_chunk:
                    pieces.append(line_chunk)
                    line_chunk = ""
                pieces.append(line[:limit])
                line = line[limit:]
            candidate = line if not line_chunk else line_chunk + "\n" + line
            if len(candidate) > limit:
                pieces.append(line_chunk)
                line_chunk = line
            else:
                line_chunk = candidate
        if line_chunk:
            pieces.append(line_chunk)

    sections: List[str] = []
    current = ""
    for piece in pieces:
        candidate = piece if not current else current + "\n\n" + piece
        if len(candidate) > limit:
            sections.append(current)
            current = piece
        else:
            current = candidate
    if current:
        sections.append(current)
    return [s for s in sections if s.strip()]


def _slack_error(e: SlackApiError) -> SlackMessageError:
    code = ""
    try:
        code = e.response.get("error") or ""
    except Exception:  # pylint: disable=broad-except
        code = ""
    status, message = _SLACK_ERRORS.get(
        code, (502, "Slack refused the message (%s)." % (code or "unknown error")))
    return SlackMessageError(code or "slack_error", message, status)


def list_channels() -> List[Dict[str, Any]]:
    """Public channels plus the private ones the bot is in, sorted by name."""
    client = slack_utils.get_client()
    channels: List[Dict[str, Any]] = []
    cursor = None
    try:
        while len(channels) < MAX_CHANNELS_LISTED:
            kwargs = {"exclude_archived": True, "limit": 1000,
                      "types": "public_channel,private_channel"}
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_list(**kwargs)
            for channel in result.get("channels") or []:
                channels.append({
                    "id": channel.get("id"),
                    "name": channel.get("name"),
                    "is_private": bool(channel.get("is_private")),
                    "is_member": bool(channel.get("is_member")),
                    "num_members": channel.get("num_members"),
                })
            cursor = (result.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        logger.warning("Slack channel list failed: %s", e.response.get("error", e))
        raise SlackMessageError(
            "slack_unavailable", "Could not load the channel list from Slack.", 502)
    channels.sort(key=lambda c: c.get("name") or "")
    return channels


def slack_id_for_email(email: Optional[str]) -> str:
    """The caller's own Slack id, for a test send."""
    user = slack_utils.get_slack_user_by_email(email) if email else None
    slack_id = (user or {}).get("id")
    if not slack_id:
        raise SlackMessageError(
            "no_slack_account",
            "No Slack account uses the email you are logged in with, so a test "
            "message has nowhere to go.", 404)
    return slack_id


def _resolve_target(channel: str) -> Dict[str, Any]:
    """A channel name, a channel id or a member id (direct message)."""
    target = (channel or "").strip().lstrip("#").strip()
    if not target:
        raise SlackMessageError("channel_required", "Choose a channel.")
    if _USER_ID_RE.match(target):
        return {"id": target, "label": "direct message", "is_dm": True}
    if _CONVERSATION_ID_RE.match(target):
        return {"id": target, "label": target, "is_dm": target.startswith("D")}
    name = target.lower()
    if not _CHANNEL_NAME_RE.match(name):
        raise SlackMessageError(
            "invalid_channel", "'%s' is not a Slack channel name." % target[:80])
    channel_id = slack_utils.get_channel_id_from_channel_name(name)
    if not channel_id:
        raise SlackMessageError(
            "channel_not_found",
            "No channel called #%s. Private channels only appear after the bot "
            "has been invited." % name, 404)
    return {"id": channel_id, "label": "#" + name, "is_dm": False}


def send_admin_message(
    message: Any, channel: Any, unfurl_links: Optional[bool] = None,
    username: Optional[str] = None, icon_emoji: Optional[str] = None,
) -> Dict[str, Any]:
    """Post ``message`` (Slack mrkdwn) and return where it landed.

    Raises SlackMessageError for anything that did not result in a post.
    """
    if not isinstance(message, str) or not message.strip():
        raise SlackMessageError("message_required", "Write a message first.")
    message = message.strip()
    if len(message) > MAX_MESSAGE_CHARS:
        raise SlackMessageError(
            "message_too_long",
            "The message is %d characters; the limit is %d."
            % (len(message), MAX_MESSAGE_CHARS))
    if not isinstance(channel, str):
        raise SlackMessageError("channel_required", "Choose a channel.")
    target = _resolve_target(channel)

    if not target["is_dm"]:
        # Public channels: joining is what stops not_in_channel. Private
        # channels refuse the join; the post then reports it by name.
        try:
            slack_utils.add_bot_to_channel(target["id"])
        except Exception as e:  # pylint: disable=broad-except
            logger.info("Could not join %s: %s", target["id"], type(e).__name__)

    kwargs: Dict[str, Any] = {
        "channel": target["id"],
        "text": message,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": section}}
                   for section in split_into_sections(message)],
        "username": (username or BOT_NAME)[:80],
    }
    if icon_emoji:
        kwargs["icon_emoji"] = icon_emoji
    else:
        kwargs["icon_url"] = BOT_ICON_URL
    if unfurl_links is not None:
        kwargs["unfurl_links"] = bool(unfurl_links)
        kwargs["unfurl_media"] = bool(unfurl_links)

    client = slack_utils.get_client()
    try:
        response = client.chat_postMessage(**kwargs)
    except SlackApiError as e:
        logger.warning("Admin Slack message to %s failed: %s",
                       target["id"], e.response.get("error", e))
        raise _slack_error(e)

    ts = response.get("ts")
    posted_channel = response.get("channel") or target["id"]
    permalink = None
    try:
        permalink = client.chat_getPermalink(
            channel=posted_channel, message_ts=ts).get("permalink")
    except Exception as e:  # pylint: disable=broad-except
        logger.info("No permalink for %s: %s", posted_channel, type(e).__name__)
    return {"channel": target["label"], "channel_id": posted_channel, "ts": ts,
            "permalink": permalink, "is_dm": target["is_dm"],
            "sections": len(kwargs["blocks"])}


def audit_admin_message(actor: Optional[Dict[str, Any]], result: Dict[str, Any],
                        message: str, test: bool = False) -> None:
    """One audit line per send. Never raises."""
    try:
        actor = actor or {}
        who = actor.get("email") or actor.get("user_id") or "unknown admin"
        preview = " ".join(str(message or "").split())[:AUDIT_PREVIEW_CHARS]
        where = "a test to themselves" if test else result.get("channel")
        slack_utils.send_slack_audit(
            action="slack_admin_message",
            message="%s sent %s (%d characters): %s" % (
                who, where, len(message or ""), preview),
            payload={"permalink": result.get("permalink")},
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("Slack message audit failed: %s", type(e).__name__)
