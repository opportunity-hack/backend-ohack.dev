"""Admin Slack composer: splitting, target resolution, error reporting."""
from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from services import slack_admin_message as svc


def _client(**overrides):
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.2", "channel": "C123456789"}
    client.chat_getPermalink.return_value = {"permalink": "https://slack/p1"}
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


@pytest.fixture
def slack():
    client = _client()
    with patch.object(svc.slack_utils, "get_client", return_value=client), \
            patch.object(svc.slack_utils, "get_channel_id_from_channel_name",
                         return_value="C123456789") as lookup, \
            patch.object(svc.slack_utils, "add_bot_to_channel") as join:
        yield {"client": client, "lookup": lookup, "join": join}


def test_short_message_is_one_untouched_section():
    text = "Line one\n\nLine two\n\n\nLine three"
    assert svc.split_into_sections(text) == [text]


def test_long_message_splits_at_blank_lines_within_the_limit():
    paragraphs = ["a" * 40, "b" * 40, "c" * 40]
    sections = svc.split_into_sections("\n\n".join(paragraphs), limit=90)
    assert sections == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]
    assert all(len(s) <= 90 for s in sections)


def test_oversized_paragraph_splits_at_lines_then_mid_line():
    sections = svc.split_into_sections("x" * 25 + "\n" + "y" * 8 + "\n" + "z" * 8, limit=10)
    assert all(len(s) <= 10 for s in sections)
    assert "".join(sections).replace("\n", "") == "x" * 25 + "y" * 8 + "z" * 8


def test_send_to_named_channel_joins_posts_and_returns_permalink(slack):
    result = svc.send_admin_message(
        ":trophy: Update\n\nSee <https://www.ohack.dev/about/completion|Definition of Done>",
        "#General", unfurl_links=False)
    slack["lookup"].assert_called_once_with("general")
    slack["join"].assert_called_once_with("C123456789")
    kwargs = slack["client"].chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C123456789"
    assert kwargs["unfurl_links"] is False and kwargs["unfurl_media"] is False
    assert kwargs["blocks"][0]["text"]["type"] == "mrkdwn"
    assert "<https://www.ohack.dev/about/completion|Definition of Done>" in \
        kwargs["blocks"][0]["text"]["text"]
    assert result["permalink"] == "https://slack/p1" and result["channel"] == "#general"


def test_member_id_is_a_direct_message_with_no_join_or_lookup(slack):
    result = svc.send_admin_message("hi", "UC31XTRT5")
    slack["lookup"].assert_not_called()
    slack["join"].assert_not_called()
    assert result["is_dm"] is True
    assert "unfurl_links" not in slack["client"].chat_postMessage.call_args.kwargs


def test_refusals_before_slack_is_called(slack):
    for message, channel, code in [
        ("", "general", "message_required"),
        ("   ", "general", "message_required"),
        ("hi", "", "channel_required"),
        ("hi", None, "channel_required"),
        ("hi", "not a channel!", "invalid_channel"),
        ("x" * (svc.MAX_MESSAGE_CHARS + 1), "general", "message_too_long"),
    ]:
        with pytest.raises(svc.SlackMessageError) as err:
            svc.send_admin_message(message, channel)
        assert err.value.code == code
    slack["client"].chat_postMessage.assert_not_called()


def test_unknown_channel_is_404(slack):
    slack["lookup"].return_value = None
    with pytest.raises(svc.SlackMessageError) as err:
        svc.send_admin_message("hi", "nope")
    assert err.value.code == "channel_not_found" and err.value.status == 404


def test_slack_refusal_is_reported_not_swallowed(slack):
    slack["client"].chat_postMessage.side_effect = SlackApiError(
        "x", {"error": "not_in_channel"})
    with pytest.raises(svc.SlackMessageError) as err:
        svc.send_admin_message("hi", "general")
    assert err.value.code == "not_in_channel" and err.value.status == 409
    slack["client"].chat_postMessage.side_effect = SlackApiError("x", {"error": "weird"})
    with pytest.raises(svc.SlackMessageError) as err:
        svc.send_admin_message("hi", "general")
    assert err.value.code == "weird" and err.value.status == 502


def test_missing_permalink_does_not_fail_the_send(slack):
    slack["client"].chat_getPermalink.side_effect = RuntimeError("boom")
    assert svc.send_admin_message("hi", "general")["permalink"] is None


def test_list_channels_follows_the_cursor_and_sorts():
    client = MagicMock()
    client.conversations_list.side_effect = [
        {"channels": [{"id": "C2", "name": "zeta", "is_member": True, "num_members": 3}],
         "response_metadata": {"next_cursor": "n"}},
        {"channels": [{"id": "C1", "name": "alpha", "is_private": True}],
         "response_metadata": {"next_cursor": ""}},
    ]
    with patch.object(svc.slack_utils, "get_client", return_value=client):
        channels = svc.list_channels()
    assert [c["name"] for c in channels] == ["alpha", "zeta"]
    assert channels[0]["is_private"] is True and channels[0]["is_member"] is False
    assert client.conversations_list.call_args_list[1].kwargs["cursor"] == "n"


def test_list_channels_failure_is_a_502():
    client = MagicMock()
    client.conversations_list.side_effect = SlackApiError("x", {"error": "ratelimited"})
    with patch.object(svc.slack_utils, "get_client", return_value=client):
        with pytest.raises(svc.SlackMessageError) as err:
            svc.list_channels()
    assert err.value.status == 502


def test_test_send_needs_a_slack_account():
    with patch.object(svc.slack_utils, "get_slack_user_by_email", return_value=None):
        with pytest.raises(svc.SlackMessageError) as err:
            svc.slack_id_for_email("a@example.com")
    assert err.value.code == "no_slack_account"
    with patch.object(svc.slack_utils, "get_slack_user_by_email",
                      return_value={"id": "U123456789"}):
        assert svc.slack_id_for_email("a@example.com") == "U123456789"


def test_audit_never_raises():
    with patch.object(svc.slack_utils, "send_slack_audit", side_effect=RuntimeError("x")):
        svc.audit_admin_message({"email": "a@b.c"}, {"channel": "#general"}, "hello")
