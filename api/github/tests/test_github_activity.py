"""
Tests for common.utils.github.get_repo_activity and its
api.github.github_service.get_github_activity wrapper.

A fake Github client stands in for PyGithub so we can assert EXACTLY 3
"API calls" are made (get_repo, get_commits().get_page(0), get_pulls().totalCount)
and control commit timestamps/authors precisely.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from github import GithubException, UnknownObjectException, RateLimitExceededException

import common.utils.github as github_utils


@pytest.fixture(autouse=True)
def _reset_rate_limit_breaker():
    """The breaker is module-global; never let one test's 403 leak into the next."""
    github_utils.reset_github_rate_limit()
    yield
    github_utils.reset_github_rate_limit()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAuthor:
    def __init__(self, login=None, avatar_url=None):
        self.login = login
        self.avatar_url = avatar_url


class FakeGitAuthor:
    def __init__(self, name, date):
        self.name = name
        self.date = date


class FakeGitCommit:
    def __init__(self, message, author):
        self.message = message
        self.author = author


class FakeCommit:
    def __init__(self, message, date, login=None, avatar_url=None, git_author_name="Someone"):
        self.author = FakeAuthor(login, avatar_url) if login else None
        self.commit = FakeGitCommit(message, FakeGitAuthor(git_author_name, date))


class FakePullsList:
    def __init__(self, calls, count):
        self._calls = calls
        self._count = count

    @property
    def totalCount(self):
        self._calls.append("get_pulls.totalCount")
        return self._count


class FakeCommitsPaginator:
    def __init__(self, calls, commits, raise_empty_409=False):
        self._calls = calls
        self._commits = commits
        self._raise_empty_409 = raise_empty_409

    def get_page(self, page):
        self._calls.append(f"get_commits.get_page({page})")
        if self._raise_empty_409:
            raise GithubException(409, {"message": "Git Repository is empty."}, None)
        return self._commits


class FakeRepo:
    def __init__(self, calls, commits, open_prs=0, raise_empty_409=False, **attrs):
        self._calls = calls
        self._commits = commits
        self._open_prs = open_prs
        self._raise_empty_409 = raise_empty_409
        self.html_url = attrs.get("html_url", "https://github.com/opportunity-hack/test-repo")
        self.default_branch = attrs.get("default_branch", "main")
        self.pushed_at = attrs.get("pushed_at")
        self.open_issues_count = attrs.get("open_issues_count", 0)
        self.stargazers_count = attrs.get("stargazers_count", 0)
        self.private = attrs.get("private", False)

    def get_commits(self):
        return FakeCommitsPaginator(self._calls, self._commits, raise_empty_409=self._raise_empty_409)

    def get_pulls(self, state="open"):
        return FakePullsList(self._calls, self._open_prs)


class FakeGithubClient:
    def __init__(self, calls, repo=None, unknown=False, rate_limited=False):
        self._calls = calls
        self._repo = repo
        self._unknown = unknown
        self._rate_limited = rate_limited

    def get_repo(self, full_name):
        self._calls.append(f"get_repo({full_name})")
        if self._unknown:
            raise UnknownObjectException(404, {"message": "Not Found"}, None)
        if self._rate_limited:
            raise RateLimitExceededException(403, {"message": "rate limited"}, {"x-ratelimit-reset": "12345"})
        return self._repo


# ---------------------------------------------------------------------------
# get_repo_activity — exact call count, math, contributors, empty repo
# ---------------------------------------------------------------------------

def _patch_github(monkeypatch, client):
    monkeypatch.setattr(github_utils, "Github", lambda *a, **kw: client)


def test_get_repo_activity_makes_exactly_three_calls(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    commits = [FakeCommit("Fix bug", now - timedelta(hours=1), login="alice")]
    repo = FakeRepo(calls, commits, open_prs=2)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("opportunity-hack", "test-repo")

    assert calls == ["get_repo(opportunity-hack/test-repo)", "get_commits.get_page(0)", "get_pulls.totalCount"]
    assert result["open_prs"] == 2


def test_get_repo_activity_last_24h_math(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    commits = [
        FakeCommit("Recent", now - timedelta(hours=2), login="alice"),
        FakeCommit("Also recent", now - timedelta(hours=23), login="bob"),
        FakeCommit("Old", now - timedelta(days=3), login="carol"),
    ]
    repo = FakeRepo(calls, commits)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert result["commits"]["total_recent"] == 3
    assert result["commits"]["last_24h"] == 2
    assert result["commits"]["last_author"] == "alice"
    assert result["commits"]["last_commit_message"] == "Recent"


def test_get_repo_activity_top_contributors_capped_at_eight(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    commits = [FakeCommit(f"c{i}", now, login=f"user{i % 10}") for i in range(30)]
    repo = FakeRepo(calls, commits)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert len(result["contributors"]) == 8
    logins = {c["login"] for c in result["contributors"]}
    assert logins.issubset({f"user{i}" for i in range(10)})


def test_get_repo_activity_falls_back_to_commit_author_name_without_github_login(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    commits = [FakeCommit("No github account", now, login=None, git_author_name="Jane Doe")]
    repo = FakeRepo(calls, commits)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert result["commits"]["last_author"] == "Jane Doe"
    assert result["contributors"][0]["login"] == "Jane Doe"


def test_get_repo_activity_excludes_repo_bootstrap_account(monkeypatch):
    """create_github_repo seeds LICENSE + README as the GITHUB_TOKEN owner
    (gregv). Those two commits must not count as team activity."""
    calls = []
    now = datetime.now(timezone.utc)
    commits = [
        FakeCommit("Add README.md", now - timedelta(minutes=5), login="gregv"),
        FakeCommit("Add MIT License", now - timedelta(minutes=6), login="GregV"),
    ]
    repo = FakeRepo(calls, commits, open_prs=0)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert result["commits"]["total_recent"] == 0
    assert result["commits"]["last_24h"] == 0
    assert result["commits"]["last_commit_at"] is None
    assert result["commits"]["last_author"] is None
    assert result["contributors"] == []


def test_get_repo_activity_last_commit_is_first_non_excluded(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    commits = [
        FakeCommit("Bootstrap tweak", now - timedelta(minutes=1), login="gregv"),
        FakeCommit("Real work", now - timedelta(hours=2), login="alice"),
        FakeCommit("Add README.md", now - timedelta(days=2), login=None, git_author_name="gregv"),
    ]
    repo = FakeRepo(calls, commits, open_prs=0)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert result["commits"]["total_recent"] == 1
    assert result["commits"]["last_author"] == "alice"
    assert result["commits"]["last_commit_message"] == "Real work"
    assert [c["login"] for c in result["contributors"]] == ["alice"]


def test_get_repo_activity_excluded_logins_env_override(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("GITHUB_ACTIVITY_EXCLUDED_LOGINS", "bot-a, Bot-B")
    commits = [
        FakeCommit("a", now, login="bot-a"),
        FakeCommit("b", now, login="bot-b"),
        FakeCommit("c", now, login="gregv"),
    ]
    repo = FakeRepo(calls, commits, open_prs=0)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "repo")

    assert result["commits"]["total_recent"] == 1
    assert result["commits"]["last_author"] == "gregv"


def test_get_repo_activity_empty_repo_returns_zeros_not_an_error(monkeypatch):
    calls = []
    repo = FakeRepo(calls, [], raise_empty_409=True)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    result = github_utils.get_repo_activity("org", "brand-new-repo")

    assert result["success"] is True
    assert result["commits"]["total_recent"] == 0
    assert result["commits"]["last_24h"] == 0
    assert result["commits"]["last_commit_at"] is None
    assert result["contributors"] == []


def test_get_repo_activity_reraises_non_409_github_exception(monkeypatch):
    calls = []
    repo = FakeRepo(calls, [], raise_empty_409=False)
    repo.get_commits = lambda: (_ for _ in ()).throw(GithubException(500, {"message": "boom"}, None))
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    with pytest.raises(GithubException):
        github_utils.get_repo_activity("org", "repo")


def test_get_repo_activity_propagates_unknown_object(monkeypatch):
    calls = []
    _patch_github(monkeypatch, FakeGithubClient(calls, unknown=True))
    with pytest.raises(UnknownObjectException):
        github_utils.get_repo_activity("org", "nope")


def test_get_repo_activity_propagates_rate_limit(monkeypatch):
    calls = []
    _patch_github(monkeypatch, FakeGithubClient(calls, rate_limited=True))
    with pytest.raises(RateLimitExceededException):
        github_utils.get_repo_activity("org", "repo")


# ---------------------------------------------------------------------------
# api.github.github_service.get_github_activity — translation + caching
# ---------------------------------------------------------------------------

import api.github.github_service as github_service


def test_get_github_activity_rejects_invalid_repo_name():
    payload, status = github_service.get_github_activity("org", "bad repo name!")
    assert status == 400
    assert payload["error"] == "invalid_repo"


def test_get_github_activity_404s_on_unknown_repo(monkeypatch):
    github_service.clear_activity_caches()
    monkeypatch.setattr(github_service, "get_repo_activity", lambda org, repo: (_ for _ in ()).throw(UnknownObjectException(404, {}, None)))
    payload, status = github_service.get_github_activity("org", "repo")
    assert status == 404
    assert payload["error"] == "repo_not_found"


def test_get_github_activity_503s_on_rate_limit(monkeypatch):
    github_service.clear_activity_caches()
    monkeypatch.setattr(
        github_service,
        "get_repo_activity",
        lambda org, repo: (_ for _ in ()).throw(RateLimitExceededException(403, {}, {"x-ratelimit-reset": "999"})),
    )
    payload, status = github_service.get_github_activity("org", "repo")
    assert status == 503
    assert payload["error"] == "github_rate_limited"


def _counting(monkeypatch, fn):
    calls = {"n": 0}

    def wrapped(org, repo):
        calls["n"] += 1
        return fn(org, repo)

    monkeypatch.setattr(github_service, "get_repo_activity", wrapped)
    return calls


def _payload(total=3, last="2026-11-15T10:00:00+00:00"):
    return {
        "success": True,
        "repo": {},
        "commits": {"total_recent": total, "last_commit_at": last},
        "contributors": [],
        "open_prs": 0,
    }


def test_get_github_activity_caches_repo_with_commits_in_long_tier(monkeypatch):
    github_service.clear_activity_caches()
    calls = _counting(monkeypatch, lambda org, repo: _payload())

    _, status1 = github_service.get_github_activity("org", "repo")
    _, status2 = github_service.get_github_activity("org", "repo")

    assert status1 == status2 == 200
    assert calls["n"] == 1
    assert ("org", "repo") in github_service._ACTIVITY_CACHE
    assert ("org", "repo") not in github_service._ACTIVITY_EMPTY_CACHE


def test_get_github_activity_empty_repo_goes_to_short_tier(monkeypatch):
    github_service.clear_activity_caches()
    calls = _counting(monkeypatch, lambda org, repo: _payload(total=0, last=None))

    payload, status = github_service.get_github_activity("org", "repo")
    github_service.get_github_activity("org", "repo")

    assert status == 200 and payload["commits"]["total_recent"] == 0
    assert calls["n"] == 1  # still cached, just in the 60s tier
    assert ("org", "repo") in github_service._ACTIVITY_EMPTY_CACHE
    assert ("org", "repo") not in github_service._ACTIVITY_CACHE
    assert github_service._ACTIVITY_EMPTY_CACHE.ttl < github_service._ACTIVITY_CACHE.ttl


@pytest.mark.parametrize("bad", [None, {}, {"success": True}, {"commits": None}, "nope"])
def test_get_github_activity_none_or_malformed_payload_is_502_not_success(monkeypatch, bad):
    github_service.clear_activity_caches()
    calls = _counting(monkeypatch, lambda org, repo: bad)

    payload, status = github_service.get_github_activity("org", "repo")
    again = github_service.get_github_activity("org", "repo")

    assert (payload, status) == ({"error": "github_unavailable"}, 502)
    assert again == (payload, status)
    assert calls["n"] == 1  # briefly cached in the error tier
    assert ("org", "repo") not in github_service._ACTIVITY_CACHE
    assert ("org", "repo") not in github_service._ACTIVITY_EMPTY_CACHE


def test_get_github_activity_errors_cached_only_in_short_error_tier(monkeypatch):
    github_service.clear_activity_caches()

    def boom(org, repo):
        raise UnknownObjectException(404, {}, None)

    calls = _counting(monkeypatch, boom)

    first = github_service.get_github_activity("org", "repo")
    second = github_service.get_github_activity("org", "repo")

    assert first == second == ({"error": "repo_not_found"}, 404)
    assert calls["n"] == 1
    assert ("org", "repo") not in github_service._ACTIVITY_CACHE
    assert github_service._ACTIVITY_ERROR_CACHE.ttl <= 30

    # Once the short error tier expires the next request retries GitHub.
    github_service._ACTIVITY_ERROR_CACHE.clear()
    github_service.get_github_activity("org", "repo")
    assert calls["n"] == 2


def test_get_github_activity_invalid_name_never_cached():
    github_service.clear_activity_caches()
    github_service.get_github_activity("org", "bad repo name!")
    assert len(github_service._ACTIVITY_ERROR_CACHE) == 0


# ---------------------------------------------------------------------------
# Private repos must never be readable through this unauthenticated endpoint
# ---------------------------------------------------------------------------

def test_get_repo_activity_refuses_private_repo_before_reading_commits(monkeypatch):
    calls = []
    repo = FakeRepo(calls, [FakeCommit("secret roadmap", datetime.now(timezone.utc), login="alice")], private=True)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    with pytest.raises(github_utils.PrivateRepoError):
        github_utils.get_repo_activity("opportunity-hack", "internal-repo")

    # Only the repo lookup happened: no commit messages / PR counts were fetched.
    assert calls == ["get_repo(opportunity-hack/internal-repo)"]


def test_get_github_activity_private_repo_looks_exactly_like_not_found(monkeypatch):
    github_service.clear_activity_caches()
    calls = []
    repo = FakeRepo(calls, [], private=True)
    _patch_github(monkeypatch, FakeGithubClient(calls, repo=repo))

    private_payload, private_status = github_service.get_github_activity("opportunity-hack", "internal-repo")

    _patch_github(monkeypatch, FakeGithubClient([], unknown=True))
    missing_payload, missing_status = github_service.get_github_activity("opportunity-hack", "does-not-exist")

    assert (private_payload, private_status) == (missing_payload, missing_status) == ({"error": "repo_not_found"}, 404)
    assert ("opportunity-hack", "internal-repo") not in github_service._ACTIVITY_CACHE


def test_get_repo_activity_uses_fast_fail_client(monkeypatch):
    """Default PyGithub retry sleeps through rate limits and pinned every
    gunicorn thread on event day — public reads must use retry=None + a
    short timeout."""
    seen = {}

    def fake_github(*args, **kwargs):
        seen.update(kwargs)
        return FakeGithubClient([], repo=FakeRepo([], []))

    monkeypatch.setattr(github_utils, "Github", fake_github)
    github_utils.get_repo_activity("opportunity-hack", "test-repo")

    assert seen["retry"] is None
    assert seen["timeout"] <= 10
    assert seen["per_page"] == 100


# ---------------------------------------------------------------------------
# Rate-limit circuit breaker + /issues (Nov 2026 outage: default PyGithub
# retry slept 972s on a 403 from GET /orgs/<org>, parking every thread)
# ---------------------------------------------------------------------------

@pytest.fixture
def closed_breaker():
    github_utils.reset_github_rate_limit()
    yield
    github_utils.reset_github_rate_limit()


def test_breaker_opens_on_rate_limit_and_blocks_further_calls(monkeypatch, closed_breaker):
    github_service.clear_activity_caches()
    reset = str(int(datetime.now(timezone.utc).timestamp()) + 600)
    calls = []

    def fake_github(*a, **kw):
        calls.append("client")
        raise RateLimitExceededException(403, {"message": "rate limited"}, {"x-ratelimit-reset": reset})

    monkeypatch.setattr(github_utils, "Github", fake_github)
    _, status = github_service.get_github_activity("org", "repo-a")
    assert status == 503
    assert github_utils.github_rate_limited_until() is not None

    # A different repo (not in any cache) never constructs a client.
    _, status = github_service.get_github_activity("org", "repo-b")
    assert status == 503
    assert calls == ["client"]


def test_breaker_pause_is_capped_and_defaults_without_header(closed_breaker):
    now = datetime.now(timezone.utc).timestamp()
    github_utils.note_github_rate_limit(RateLimitExceededException(403, {}, {"x-ratelimit-reset": str(int(now) + 99999)}))
    assert github_utils.github_rate_limited_until() <= now + 3600 + 1
    github_utils.reset_github_rate_limit()
    github_utils.note_github_rate_limit(RateLimitExceededException(403, {}, {}))
    assert now + 30 < github_utils.github_rate_limited_until() <= now + 61


class FakeIssue:
    def __init__(self, n, pr=False):
        self.number = n
        self.title = f"Issue {n}"
        self.body = ""
        self.state = "open"
        self.created_at = None
        self.updated_at = None
        self.pull_request = object() if pr else None


def test_get_issues_skips_org_lookup_filters_prs_and_caps(monkeypatch, closed_breaker):
    seen = {}

    class Repo:
        def get_issues(self, state=None):
            seen["state"] = state
            return [FakeIssue(i, pr=(i % 2 == 0)) for i in range(2000)]

    class Client:
        def get_repo(self, full_name):
            seen["repo"] = full_name
            return Repo()

        def get_organization(self, name):  # pragma: no cover - must not be called
            raise AssertionError("get_issues must not call GET /orgs/<org>")

    def fake_github(*a, **kw):
        seen["kwargs"] = kw
        return Client()

    monkeypatch.setattr(github_utils, "Github", fake_github)
    issues = github_utils.get_issues("repo", "org", state="ALL")

    assert seen["repo"] == "org/repo" and seen["state"] == "all"
    assert seen["kwargs"]["retry"] is None and seen["kwargs"]["per_page"] == 100
    assert len(issues) == github_utils.MAX_ISSUES_SCANNED // 2  # PRs dropped, scan capped
    assert all(i["issue_number"] % 2 == 1 for i in issues)


def test_get_github_issues_caches_rate_limit_briefly_and_opens_breaker(monkeypatch, closed_breaker):
    github_service._ISSUES_CACHE.clear()
    github_service._ISSUES_ERROR_CACHE.clear()
    calls = {"n": 0}

    class Client:
        def get_repo(self, full_name):
            calls["n"] += 1
            raise RateLimitExceededException(403, {"message": "rate limited"}, {})

    monkeypatch.setattr(github_utils, "Github", lambda *a, **kw: Client())

    first = github_service.get_github_issues("org", "repo", "all")
    second = github_service.get_github_issues("org", "repo", "all")

    assert first == second == {"error": "github_rate_limited"}
    assert calls["n"] == 1
    assert github_utils.github_rate_limited_until() is not None
    assert ("org", "repo", "all") not in github_service._ISSUES_CACHE
