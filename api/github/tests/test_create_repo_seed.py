"""create_github_repo(seed_files=...) commits the rendered seed instead of the
legacy README, and one failing file never aborts the approval.

A fake PyGithub org/repo records create_file calls; nothing touches GitHub.
"""
import os

import pytest
from github import GithubException

os.environ.setdefault("ENVIRONMENT", "test")

import common.utils.github as github_utils  # noqa: E402


class FakeRepo:
    def __init__(self, name, fail_paths=()):
        self.name = name
        self.files = []
        self.collaborators = []
        self.description = None
        self._fail_paths = set(fail_paths)

    def add_to_collaborators(self, login, permission=None):
        self.collaborators.append((login, permission))

    def create_file(self, path, message, content):
        if path in self._fail_paths:
            raise GithubException(422, {"message": f"boom on {path}"}, None)
        self.files.append((path, message, content))

    def edit(self, description=None):
        self.description = description


class FakeOrg:
    def __init__(self, repo):
        self._repo = repo
        self.created = []

    def get_repo(self, name):
        raise GithubException(404, {"message": "Not Found"}, None)

    def create_repo(self, name, private=False):
        self.created.append((name, private))
        return self._repo


def _patch(monkeypatch, repo):
    org = FakeOrg(repo)
    monkeypatch.setattr(github_utils, "Github", lambda *a, **kw: object())
    monkeypatch.setattr(github_utils, "get_github_organization", lambda g, name: org)
    return org


def _call(seed_files):
    return github_utils.create_github_repo(
        "TeamSparkle-CodinginColor", "fall-2026", "Greg V", "Team Sparkle", "team-sparkle",
        "gregv", "Coding in Color", "np-1", "Opportunity-Hack-2026", "", seed_files=seed_files,
    )


def test_seed_files_are_committed_in_order_and_legacy_readme_is_not(monkeypatch):
    repo = FakeRepo("TeamSparkle-CodinginColor")
    org = _patch(monkeypatch, repo)
    seed = [
        {"path": "LICENSE", "message": "Add MIT License", "content": "MIT License\n\nPermission is hereby granted"},
        {"path": "README.md", "message": "Add README", "content": "# Team Sparkle — Coding in Color"},
        {"path": ".claude/settings.json", "message": "Add hooks", "content": "{}"},
    ]

    result = _call(seed)

    assert org.created == [("TeamSparkle-CodinginColor", False)]
    assert [f[0] for f in repo.files] == ["LICENSE", "README.md", ".claude/settings.json"]
    readme = next(c for p, _, c in repo.files if p == "README.md")
    assert "Checklist for the final submission" not in readme  # legacy README not written
    assert repo.description == "Team Sparkle for Coding in Color — fall-2026 Opportunity Hack"
    assert result == {"repo_name": "TeamSparkle-CodinginColor",
                      "full_url": "https://github.com/Opportunity-Hack-2026/TeamSparkle-CodinginColor"}
    assert ("gregv", "admin") in repo.collaborators


def test_one_failing_seed_file_does_not_abort_the_rest(monkeypatch):
    repo = FakeRepo("r", fail_paths={".github/workflows/pr-lint.yml"})
    _patch(monkeypatch, repo)
    seed = [
        {"path": "README.md", "message": "m", "content": "a"},
        {"path": ".github/workflows/pr-lint.yml", "message": "m", "content": "b"},
        {"path": ".gitignore", "message": "m", "content": "c"},
    ]

    _call(seed)

    assert [f[0] for f in repo.files] == ["README.md", ".gitignore"]


def test_without_seed_files_the_legacy_readme_and_license_are_written(monkeypatch):
    repo = FakeRepo("r")
    _patch(monkeypatch, repo)

    _call(None)

    paths = [f[0] for f in repo.files]
    assert paths == ["LICENSE", "README.md"]
    assert "Checklist for the final submission" in repo.files[1][2]
