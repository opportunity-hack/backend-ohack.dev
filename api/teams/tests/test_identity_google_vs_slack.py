"""Frontend issue #501 — one person, one PropelAuth account, two logins
(Google and Slack, same email).

The profile endpoints found the user doc by the stored PropelAuth id; the team
endpoints found it by the CURRENT login's provider id. When the two disagreed:

  * create team   -> 400 user_not_found ("We couldn't find your profile")
  * /team/<e>/me  -> no teams (the dashboard said "no team")
  * leave team    -> "User not found"
  * an older account with no stored PropelAuth id got a SECOND user doc, so
    the roster showed a different account than the person's own profile

Everything now goes through `users_service.resolve_caller`. Part 1 runs the
real resolver against MockFirestore (only the provider call is faked). Part 2
runs the team functions against the tiny FakeDB with the caller injected,
because MockFirestore cannot run transactions.
"""
import importlib
import os
import sys

os.environ.setdefault("ENVIRONMENT", "test")  # -> MockFirestore

import pytest

import api.teams.teams_service as team_api
import services.feedback_service as feedback_svc
import services.teams_service as team_svc
import services.users_service as us
from api.teams.tests._fake_firestore import FakeDB, FakeTransaction
from common.utils.oauth_providers import get_oauth_provider_from_propel_response
from db.db import get_db
from test.common.auth_stubs import passthrough_auth_module

GOOGLE_SUB = "oauth2|google-oauth2|501501501"
SLACK_SUB = us.USER_ID_PREFIX + "U501SAM"


def _seed_user(db_id, user_id, propel_id, email, name="Sam"):
    doc = {
        "user_id": user_id,
        "email_address": email,
        "profile_image": "https://i.imgur.com/RdOsE7s.png",
        "name": name,
        "nickname": "Sam",
        "badges": [],
        "teams": [],
    }
    if propel_id:
        doc["propel_id"] = propel_id
    get_db().collection("users").document(db_id).set(doc)


def _login_as(monkeypatch, sub, email, name="Sam"):
    """What the provider round-trip returns for the CURRENT login."""
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id",
                        lambda _pid: {"sub": sub, "email": email, "name": name, "given_name": "Sam"})


def _users_with_email(email):
    return [d.id for d in get_db().collection("users").where("email_address", "==", email).stream()]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for mod in (us, team_api, team_svc, feedback_svc):
        monkeypatch.setattr(mod, "send_slack_audit", lambda *a, **k: None, raising=False)
    us.get_profile_metadata.cache_clear()
    yield
    us.get_profile_metadata.cache_clear()


# --------------------------------------------------------------------------
# Part 1 — the resolver, real code against MockFirestore
# --------------------------------------------------------------------------

def test_slack_is_picked_whenever_both_tokens_are_present():
    # Provider detection is order-based, not "which login is current" — one
    # more reason the provider id can't be how we find a person's user doc.
    provider, _ = get_oauth_provider_from_propel_response({
        "google": {"access_token": "ya29"},
        "slack": {"access_token": "xoxp"},
    })
    assert provider == "slack"


def test_caller_is_the_profile_doc_after_switching_from_google_to_slack(monkeypatch):
    _seed_user("g501a", GOOGLE_SUB + "1", "propel-501-a", "a501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "1", "a501@example.com")

    caller = us.resolve_caller("propel-501-a")

    assert caller.user.id == us.get_profile_metadata("propel-501-a")["id"] == "g501a"
    assert caller.doc_ids == {"g501a"}
    # The Slack id still comes from the live login — for invites only.
    assert caller.slack_user_id == SLACK_SUB + "1"
    assert _users_with_email("a501@example.com") == ["g501a"]  # no second doc


def test_caller_owns_both_docs_when_two_already_exist(monkeypatch):
    """The old login path made one doc per provider. Either may be on a roster."""
    _seed_user("a501google", GOOGLE_SUB + "2", "propel-501-b", "b501@example.com")
    _seed_user("b501slack", SLACK_SUB + "2", "propel-501-b", "b501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "2", "b501@example.com")

    caller = us.resolve_caller("propel-501-b")

    assert caller.user.id == us.get_profile_metadata("propel-501-b")["id"]
    assert caller.doc_ids == {"a501google", "b501slack"}
    assert caller.owns("a501google") and caller.owns("b501slack")
    assert not caller.owns("someone-else")


def test_older_account_without_propel_id_is_adopted_not_duplicated(monkeypatch):
    """Signed up with Slack before propel_id was stored; now signs in with
    Google. Same verified email -> same person -> same doc."""
    _seed_user("s501c", SLACK_SUB + "3", None, "c501@example.com")
    _login_as(monkeypatch, GOOGLE_SUB + "3", "c501@example.com")

    caller = us.resolve_caller("propel-501-c")

    assert caller.user.id == "s501c"
    assert _users_with_email("c501@example.com") == ["s501c"]
    assert get_db().collection("users").document("s501c").get().to_dict()["propel_id"] == "propel-501-c"
    # Their Slack id is still known from the doc, so channel invites work
    # even though this login is Google.
    assert caller.slack_user_id == SLACK_SUB + "3"


def test_doc_found_by_provider_id_gets_its_propel_id_stamped(monkeypatch):
    _seed_user("s501d", SLACK_SUB + "4", None, "d501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "4", "d501@example.com")

    us.resolve_caller("propel-501-d")

    assert get_db().collection("users").document("s501d").get().to_dict()["propel_id"] == "propel-501-d"


def test_blank_email_never_links_strangers(monkeypatch):
    """queue_team saves placeholder docs for invited teammates with email ""."""
    _seed_user("placeholder501", SLACK_SUB + "5x", None, "")
    _seed_user("g501e", GOOGLE_SUB + "5", "propel-501-e", "e501@example.com")
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda _pid: None)  # provider down

    caller = us.resolve_caller("propel-501-e")

    assert caller.doc_ids == {"g501e"}
    assert caller.slack_user_id is None


def test_unknown_person_resolves_to_none(monkeypatch):
    monkeypatch.setattr(us, "get_oauth_user_from_propel_user_id", lambda _pid: None)
    monkeypatch.setattr(us, "_fetch_propel_metadata", lambda _pid: None)
    assert us.resolve_caller("propel-501-nobody") is None
    assert us.resolve_caller(None) is None


def _open_create_team_gates(monkeypatch):
    monkeypatch.setattr(team_api, "get_hackathon_by_event_id", lambda _e: {"event_id": "fall-2026"})
    monkeypatch.setattr(team_api, "get_team_project_options", lambda _e: {"nonprofits": []})
    monkeypatch.setattr(team_api, "validate_project_rankings", lambda _r, _o: [])
    monkeypatch.setattr(team_api, "validate_github_username", lambda _n: True)


def test_create_team_after_switching_from_google_to_slack(monkeypatch):
    """The exact refusal from the issue: profile works, final submit said
    "We couldn't find your profile"."""
    propel_id = "propel-501-create"
    _seed_user("g501create", GOOGLE_SUB, propel_id, "create501@example.com")
    _login_as(monkeypatch, SLACK_SUB, "create501@example.com")
    _open_create_team_gates(monkeypatch)

    created = []
    monkeypatch.setattr(team_api, "create_slack_channel", lambda name: created.append(name) or "C1")

    class _PastIdentity(Exception):
        pass

    def _stop(_cid):
        raise _PastIdentity()  # identity resolved; the rest isn't under test

    monkeypatch.setattr(team_api, "add_bot_to_channel", _stop)

    try:
        result = team_api.queue_team(propel_id, {
            "name": "Rockets", "slackChannel": "rockets-501",
            "eventId": "fall-2026", "githubUsername": "sam",
        })
    except _PastIdentity:
        result = None

    assert result is None, f"queue_team refused: {result}"
    assert created == ["rockets-501"]


def test_create_team_refusal_no_longer_gives_false_advice(monkeypatch):
    _open_create_team_gates(monkeypatch)
    monkeypatch.setattr(team_api, "resolve_caller", lambda _pid: None)

    body, status = team_api.queue_team("propel-501-x", {
        "name": "Rockets", "slackChannel": "rockets-501x",
        "eventId": "fall-2026", "githubUsername": "sam",
    })

    assert status == 400 and body["error"] == "user_not_found"
    assert "Open your profile page" not in body["message"]


def test_privacy_settings_follow_the_person_not_the_provider(monkeypatch):
    _seed_user("g501priv", GOOGLE_SUB + "7", "propel-501-priv", "priv501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "7", "priv501@example.com")
    monkeypatch.setattr(us, "clear_portfolio_caches", lambda *_a, **_k: None)

    assert us.get_privacy_settings("propel-501-priv") is not None  # was None
    assert us.update_privacy_settings("propel-501-priv", {}) is not None


def test_my_feedback_follows_the_person_not_the_provider(monkeypatch):
    _seed_user("g501fb", GOOGLE_SUB + "8", "propel-501-fb", "fb501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "8", "fb501@example.com")
    get_db().collection("feedback").document("fb501").set({
        "feedback_receiver_id": "g501fb", "timestamp": "2026-11-01T00:00:00", "feedback": {"note": "great"},
    })

    result = feedback_svc.get_user_feedback("propel-501-fb")  # used to raise

    assert [f["feedback"] for f in result["feedback"]] == [{"note": "great"}]


def test_planning_board_seed_reuses_the_existing_doc(monkeypatch):
    """The board's "self-heal" used to insert a second, empty doc keyed on the
    PropelAuth id for anyone who already had a Slack or Google doc."""
    # The real common.auth builds a PropelAuth client at import; stub it the
    # way the route tests do, and drop the module again afterwards.
    monkeypatch.setitem(sys.modules, "common.auth", passthrough_auth_module())
    monkeypatch.delitem(sys.modules, "api.planning.planning_views", raising=False)
    planning_views = importlib.import_module("api.planning.planning_views")
    monkeypatch.delitem(sys.modules, "api.planning.planning_views")

    _seed_user("s501plan", SLACK_SUB + "9", None, "plan501@example.com")
    _login_as(monkeypatch, SLACK_SUB + "9", "plan501@example.com")

    planning_views._seed_firestore_user_from_oauth("propel-501-plan", {"sub": SLACK_SUB + "9"})

    assert _users_with_email("plan501@example.com") == ["s501plan"]
    assert [d.id for d in get_db().collection("users").where("user_id", "==", "propel-501-plan").stream()] == []
    assert get_db().collection("users").document("s501plan").get().to_dict()["propel_id"] == "propel-501-plan"


# --------------------------------------------------------------------------
# Part 2 — team functions, caller injected, FakeDB (supports transactions)
# --------------------------------------------------------------------------

class _User:
    def __init__(self, doc_id, name="Sam"):
        self.id = doc_id
        self.name = name
        self.user_id = GOOGLE_SUB


def _caller(doc_ids, primary, slack_user_id=None):
    return us.Caller(_User(primary), set(doc_ids), slack_user_id)


@pytest.fixture
def fake(monkeypatch):
    db = FakeDB()
    db.transaction = lambda: FakeTransaction()
    slack = {"invites": [], "messages": []}
    for mod in (team_api, team_svc):
        monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(team_svc.firestore, "transactional", lambda fn: fn)
    monkeypatch.setattr(team_svc, "_clear_cache", lambda: None)
    monkeypatch.setattr(team_svc, "invite_user_to_channel", lambda uid, ch: slack["invites"].append((uid, ch)))
    monkeypatch.setattr(team_svc, "send_slack", lambda msg, ch: slack["messages"].append(msg))
    db.slack = slack
    return db


def _team(db, team_id, member_ids, channel="rockets"):
    for uid in set(member_ids) | {"google-doc", "slack-doc"}:
        if db.doc("users", uid) is None:
            db.put("users", uid, {"teams": []})
    for uid in member_ids:
        db.store[("users", uid)]["teams"] = [db.ref("teams", team_id)]
    return db.put("teams", team_id, {
        "name": "Rockets", "slack_channel": channel, "status": "IN_REVIEW",
        "users": [db.ref("users", uid) for uid in member_ids],
    })


def _roster(db, team_id):
    return [ref.id for ref in db.doc("teams", team_id)["users"]]


def test_join_adds_the_profile_doc_and_invites_the_slack_account(fake, monkeypatch):
    _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_svc, "resolve_caller",
                        lambda _pid: _caller({"google-doc"}, "google-doc", slack_user_id=SLACK_SUB))

    msg = team_svc.join_team("propel-501", {"teamId": "t1"})

    assert msg.text == "Joined Team"
    assert _roster(fake, "t1") == ["other", "google-doc"]
    assert [t.id for t in fake.doc("users", "google-doc")["teams"]] == ["t1"]
    assert fake.slack["invites"] == [(SLACK_SUB, "rockets")]
    assert "<@U501SAM>" in fake.slack["messages"][0]


def test_join_without_a_slack_login_posts_the_name_and_invites_nobody(fake, monkeypatch):
    _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_svc, "resolve_caller", lambda _pid: _caller({"google-doc"}, "google-doc"))

    assert team_svc.join_team("propel-501", {"teamId": "t1"}).text == "Joined Team"
    assert fake.slack["invites"] == []
    assert fake.slack["messages"] == ["Sam has joined the team!"]


def test_join_does_not_list_one_person_twice(fake, monkeypatch):
    """Joined as the Slack doc, now resolves to the Google doc."""
    _team(fake, "t1", ["slack-doc"])
    monkeypatch.setattr(team_svc, "resolve_caller",
                        lambda _pid: _caller({"google-doc", "slack-doc"}, "google-doc"))

    assert team_svc.join_team("propel-501", {"teamId": "t1"}).text == "User was already in the team"
    assert _roster(fake, "t1") == ["slack-doc"]


def test_join_reports_an_unresolvable_caller(fake, monkeypatch):
    _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_svc, "resolve_caller", lambda _pid: None)
    assert team_svc.join_team("propel-501", {"teamId": "t1"}).text == "Error: User not found"


def test_leave_removes_whichever_of_my_docs_is_on_the_roster(fake, monkeypatch):
    """Joined with Google, signs in with Slack, presses Leave — used to be
    "User not found"."""
    _team(fake, "t1", ["other", "slack-doc"])
    monkeypatch.setattr(team_svc, "resolve_caller",
                        lambda _pid: _caller({"google-doc", "slack-doc"}, "google-doc"))

    assert team_svc.unjoin_team("propel-501", {"teamId": "t1"}).text == "Removed from Team"
    assert _roster(fake, "t1") == ["other"]
    assert fake.doc("users", "slack-doc")["teams"] == []
    assert fake.doc("users", "other")["teams"] != []  # teammates untouched


def test_leave_when_not_on_the_team(fake, monkeypatch):
    _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_svc, "resolve_caller", lambda _pid: _caller({"google-doc"}, "google-doc"))

    assert team_svc.unjoin_team("propel-501", {"teamId": "t1"}).text == "User was not in the team"
    assert _roster(fake, "t1") == ["other"]


def test_my_teams_finds_a_team_joined_with_the_other_provider(fake, monkeypatch):
    mine = _team(fake, "t1", ["other", "slack-doc"])
    theirs = _team(fake, "t2", ["other"])
    monkeypatch.setattr(team_api, "get_hackathon_by_event_id", lambda _e: {"teams": [mine, theirs]})
    monkeypatch.setattr(team_api, "resolve_caller",
                        lambda _pid: _caller({"google-doc", "slack-doc"}, "google-doc"))

    result = team_api.get_my_teams_by_event_id("propel-501", "fall-2026")

    assert [t["id"] for t in result["teams"]] == ["t1"]


def test_my_teams_is_empty_not_everyones_when_the_caller_is_unknown(fake, monkeypatch):
    """The old code compared `None == None` and could match members whose doc
    had no user_id when the provider call failed."""
    team = _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_api, "get_hackathon_by_event_id", lambda _e: {"teams": [team]})
    monkeypatch.setattr(team_api, "resolve_caller", lambda _pid: None)

    assert team_api.get_my_teams_by_event_id("propel-501", "fall-2026")["teams"] == []


@pytest.mark.parametrize("doc_ids,expected", [
    ({"google-doc", "slack-doc"}, True),   # roster holds my other doc
    ({"google-doc"}, False),               # not on the team
])
def test_user_is_on_team(fake, monkeypatch, doc_ids, expected):
    _team(fake, "t1", ["other", "slack-doc"])
    monkeypatch.setattr(team_api, "resolve_caller", lambda _pid: _caller(doc_ids, "google-doc"))
    assert team_api.user_is_on_team("propel-501", "t1") is expected


def test_user_is_on_team_is_false_when_the_caller_is_unknown(fake, monkeypatch):
    _team(fake, "t1", ["other"])
    monkeypatch.setattr(team_api, "resolve_caller", lambda _pid: None)
    assert team_api.user_is_on_team("propel-501", "t1") is False
    assert team_api.user_is_on_team("propel-501", "missing-team") is False


def test_completion_message_names_the_person(monkeypatch):
    # get_propel_user_details_by_id returns a tuple; the old code called
    # .get() on it, so every message said "A teammate".
    monkeypatch.setattr(team_api, "get_propel_user_details_by_id",
                        lambda _pid: ("sam@example.com", GOOGLE_SUB, "", "", "Sam Rivera", "Sam"))
    assert team_api._completer_name("propel-501") == "Sam Rivera"
