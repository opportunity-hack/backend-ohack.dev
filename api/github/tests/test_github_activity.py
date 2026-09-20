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
    github_service._ACTIVITY_CACHE.clear()
    monkeypatch.setattr(github_service, "get_repo_activity", lambda org, repo: (_ for _ in ()).throw(UnknownObjectException(404, {}, None)))
    payload, status = github_service.get_github_activity("org", "repo")
    assert status == 404
    assert payload["error"] == "repo_not_found"


def test_get_github_activity_503s_on_rate_limit(monkeypatch):
    github_service._ACTIVITY_CACHE.clear()
    monkeypatch.setattr(
        github_service,
        "get_repo_activity",
        lambda org, repo: (_ for _ in ()).throw(RateLimitExceededException(403, {}, {"x-ratelimit-reset": "999"})),
    )
    payload, status = github_service.get_github_activity("org", "repo")
    assert status == 503
    assert payload["error"] == "github_rate_limited"


def test_get_github_activity_caches_success_only(monkeypatch):
    github_service._ACTIVITY_CACHE.clear()
    call_count = {"n": 0}

    def fake_activity(org, repo):
        call_count["n"] += 1
        return {"success": True, "repo": {}, "commits": {}, "contributors": [], "open_prs": 0}

    monkeypatch.setattr(github_service, "get_repo_activity", fake_activity)

    payload1, status1 = github_service.get_github_activity("org", "repo")
    payload2, status2 = github_service.get_github_activity("org", "repo")

    assert status1 == status2 == 200
    assert call_count["n"] == 1  # second call served from cache


def test_get_github_activity_does_not_cache_errors(monkeypatch):
    github_service._ACTIVITY_CACHE.clear()
    call_count = {"n": 0}

    def fake_activity(org, repo):
        call_count["n"] += 1
        raise UnknownObjectException(404, {}, None)

    monkeypatch.setattr(github_service, "get_repo_activity", fake_activity)

    github_service.get_github_activity("org", "repo")
    github_service.get_github_activity("org", "repo")

    assert call_count["n"] == 2  # never cached, so it's retried
