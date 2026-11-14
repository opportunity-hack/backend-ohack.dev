"""Channel name → id lookup (common.utils.slack).

Regression (Nov 2026): the lookup cached misses for 60s. queue_team checks the
name before creating the channel, so the None stuck, and every invite into the
brand-new team channel logged "not found" and was skipped — channels ended up
with only the bot. Also: the pagination loop never sent next_cursor, so a
paged miss re-fetched page 1 forever.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

import common.utils.slack as slack


class FakeClient:
    def __init__(self, pages):
        self.pages = pages  # list of lists of channel dicts
        self.list_calls = []
        self.created = []

    def conversations_list(self, **kwargs):
        self.list_calls.append(kwargs)
        cursor = kwargs.get("cursor")
        index = int(cursor) if cursor else 0
        channels = self.pages[index] if index < len(self.pages) else []
        next_cursor = str(index + 1) if index + 1 < len(self.pages) else ""
        return {"channels": channels, "response_metadata": {"next_cursor": next_cursor}}

    def conversations_create(self, name):
        self.created.append(name)
        channel = {"name": name, "id": "CNEW00001"}
        self.pages[0].append(channel)
        return {"channel": channel}


@pytest.fixture
def client(monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_WEBHOOK", "SLACK_USER_TOKEN"):
        monkeypatch.setenv(var, "")
    fake = FakeClient([[{"name": "general", "id": "CGENERAL01"}]])
    monkeypatch.setattr(slack, "get_client", lambda: fake)
    slack._CHANNEL_ID_CACHE.clear()
    yield fake
    slack._CHANNEL_ID_CACHE.clear()


def test_a_miss_is_not_cached(client):
    assert slack.get_channel_id_from_channel_name("rockets") is None
    client.pages[0].append({"name": "rockets", "id": "CROCKETS01"})
    assert slack.get_channel_id_from_channel_name("rockets") == "CROCKETS01"


def test_create_then_lookup_finds_the_new_channel_without_listing(client):
    channel_id = slack.create_slack_channel("rockets")
    assert channel_id == "CNEW00001"
    calls_before = len(client.list_calls)
    assert slack.get_channel_id_from_channel_name("rockets") == "CNEW00001"
    assert len(client.list_calls) == calls_before


def test_hits_are_cached(client):
    assert slack.get_channel_id_from_channel_name("general") == "CGENERAL01"
    calls_before = len(client.list_calls)
    assert slack.get_channel_id_from_channel_name("general") == "CGENERAL01"
    assert len(client.list_calls) == calls_before


def test_pagination_follows_the_cursor(client):
    client.pages = [
        [{"name": "a", "id": "CAAAAAAAA1"}],
        [{"name": "b", "id": "CBBBBBBBB1"}],
        [{"name": "target", "id": "CTARGET001"}],
    ]
    assert slack.get_channel_id_from_channel_name("target") == "CTARGET001"
    assert [c.get("cursor") for c in client.list_calls] == [None, "1", "2"]
    assert all(c["limit"] == 1000 for c in client.list_calls)


def test_paged_miss_terminates(client):
    client.pages = [[{"name": "a", "id": "CAAAAAAAA1"}], [{"name": "b", "id": "CBBBBBBBB1"}]]
    assert slack.get_channel_id_from_channel_name("nope") is None
    assert len(client.list_calls) == 2


def test_send_slack_skips_the_lookup_for_an_id(client, monkeypatch):
    posted = []
    client.chat_postMessage = lambda **kwargs: posted.append(kwargs) or {"ok": True}
    slack.send_slack("hi", "CNEW00001")
    assert client.list_calls == []
    assert posted and posted[0]["channel"] == "CNEW00001"
