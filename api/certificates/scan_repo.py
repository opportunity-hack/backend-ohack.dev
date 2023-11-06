import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from typing import List

from git import Repo


@dataclass
class GitFameRow:
    author: str
    linesOfCode: int
    commits: int
    files: int
    linesOfCodeDist: float
    commitsDist: float
    filesDist: float


@dataclass
class GitFameTable:
    totalCommits: int
    totalCtimes: int
    totalFiles: int
    totalLinesOfCode: int
    authors: List[GitFameRow]


def _parseTotalLine(line: str) -> int:
    return int(re.search(r"([0-9]+)|$", line).group())


def _parseGitFameRow(row: str) -> GitFameRow:
    rowSplit: List[str] = list(
        map(lambda x: x.strip(), row.split("|")[1:-1])
    )
    (author, linesOfCode, commits, files, dist) = rowSplit
    distList: List[str] = list(
        map(lambda x: float(x), dist.split("/"))
    )
    (linesOfCodeDist, commitsDist, filesDist) = distList
    return GitFameRow(
        author,
        int(linesOfCode),
        int(commits),
        int(files),
        float(linesOfCodeDist),
        float(commitsDist),
        float(filesDist)
    )


def _parseGitFameResults(gitFameOutput: bytes) -> List[GitFameRow]:
    gitFameStr: str = gitFameOutput.decode("UTF-8")
    (totalCommits, totalCtimes, totalFiles,
     totalLoc, *table) = gitFameStr.split("\n")
    tableRows: List[str] = table[2:-1]
    authorInformation: List[GitFameRow] = [
        _parseGitFameRow(row) for row in tableRows]
    return GitFameTable(
        _parseTotalLine(totalCommits),
        _parseTotalLine(totalCtimes),
        _parseTotalLine(totalFiles),
        _parseTotalLine(totalLoc),
        authorInformation
    )


def _pullRepository(repoUrl: str) -> str:
    """Downloads a remote Github repository locally."""
    saveLoc: str = os.path.join("/tmp", f"GitPull-{uuid.uuid4()}")
    # WARNING: Possible command injection, testing needed!
    Repo.clone_from(repoUrl, saveLoc)
    return saveLoc


def _runGitFame(repoLoc: str) -> GitFameTable:
    """Scans a local repository with the GitFame tool"""
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git-fame", repoLoc], stdout=subprocess.PIPE)
    if (result.stderr):
        return None
    return _parseGitFameResults(result.stdout)


def _removePulledRepo(repoLoc: str) -> None:
    """Helper function that deletes a directory and all subfolders."""
    shutil.rmtree(repoLoc)


def getGitFameData(repositoryURL: str) -> GitFameTable:
    """Pull and scan a GitHub repository using the Gitfame tool.

    Parameters:
        repositoryURL (str): a string representation of the GitHub URL to be scraped
    
    Returns:
        A GitFameTable dataclass object containing the parsed GitFame output
    """
    saveLoc: str = _pullRepository(repositoryURL)
    results: GitFameTable = _runGitFame(saveLoc)
    _removePulledRepo(saveLoc)
    return results