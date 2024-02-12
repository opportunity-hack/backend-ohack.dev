import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from typing import List

from git import Repo
import json


@dataclass
class GitFameRow:
    author: str
    hours: float
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
    totalHours: float
    authors: List[GitFameRow]    

@dataclass
class GitFameTableCombined:
    totalCommits: int
    totalCtimes: int
    totalFiles: int
    totalLinesOfCode: int
    totalHours: float
    authors: List[GitFameRow]
    authorsEmails: List[GitFameRow]

def _parseTotalLine(line: str) -> int:
    return int(re.search(r"([0-9]+)|$", line).group())


def _parseGitFameRow(row: str) -> GitFameRow:        
    author, hrs, linesOfCode, commits, files, linesOfCodeDist, commitsDist, filesDist = row
    
    return GitFameRow(
        author,
        round(float(hrs),1),
        int(linesOfCode),
        int(commits),
        int(files),
        float(linesOfCodeDist),
        float(commitsDist),
        float(filesDist)
    )


def _parseGitFameResults(gitFameOutput: bytes) -> List[GitFameRow]:
    gitFameStr: str = gitFameOutput.decode("UTF-8")
    gitFameJson: dict = json.loads(gitFameStr)

    '''
    Example output:
    {
        "total": {
            "loc": 79788,
            "files": 146,
            "ctimes": 342,
            "commits": 172,
            "hours": "40.0"
        },
        "data": [
            ["Gabe Jimenez", 5.990555555555556, 33145, 33, 34, 41.5, 19.2, 23.3],
            ["aitzeng", 7.089722222222222, 16061, 27, 28, 20.1, 15.7, 19.2],
            ["David Tran", 6.433055555555556, 14770, 24, 32, 18.5, 14.0, 21.9],
            ["Maximus Chen", 10.258055555555556, 14646, 43, 25, 18.4, 25.0, 17.1],
            ["John Novakowski", 6.191944444444444, 1129, 35, 24, 1.4, 20.3, 16.4],
            ["Greg V", 2.0, 32, 2, 2, 0.0, 1.2, 1.4],
            ["Anthony Tzeng", 2.0, 5, 8, 1, 0.0, 4.7, 0.7]
        ],
        "columns": ["Author", "hrs", "loc", "coms", "fils", "%loc", "%coms", "%fils"]
    }
    '''

    (totalCommits, totalCtimes, totalFiles, totalLoc, totalHours) = gitFameJson["total"]["commits"], gitFameJson["total"]["ctimes"], gitFameJson["total"]["files"], gitFameJson["total"]["loc"], round(float(gitFameJson["total"]["hours"]), 1)

    tableRows: List[str] = gitFameJson["data"]
    
    authorInformation: List[GitFameRow] = [
        _parseGitFameRow(row) for row in tableRows]
    
    return GitFameTable(
        totalCommits,
        totalCtimes,
        totalFiles,
        totalLoc,
        totalHours,
        authorInformation
    )


def _pullRepository(repoUrl: str) -> str:
    """Downloads a remote Github repository locally."""
    saveLoc: str = os.path.join("/tmp", f"GitPull-{uuid.uuid4()}")
    # WARNING: Possible command injection, testing needed!
    repo: Repo = Repo.clone_from(repoUrl, saveLoc)    
    return saveLoc if (repo is not None) else ""


def _runGitFame(repoLoc: str, showEmail: bool) -> GitFameTable:
    """Scans a local repository with the GitFame tool"""
    
    args = ["git-fame", "--cost=hours", "--format=json"]
    if showEmail:
        args.append("-e")
        
    args.append(repoLoc)
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        args, stdout=subprocess.PIPE)
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
    results: GitFameTable = _runGitFame(saveLoc, False)

    # FIXME: This is pretty hacky to get the email address along with the name 
    # We run this again, but with the email flag set to True
    # As of 12FEB2024: There is no current way to get this as a single output with git-fame
    resultsWithEmail: GitFameTable = _runGitFame(saveLoc, True)

    # Combine results and resultsWithEmail into GitFameTableCombined    
    resultsCombined: GitFameTableCombined = GitFameTableCombined(
        results.totalCommits,
        results.totalCtimes,
        results.totalFiles,
        results.totalLinesOfCode,
        results.totalHours,
        results.authors,
        resultsWithEmail.authors
    )


    _removePulledRepo(saveLoc)
    return resultsCombined