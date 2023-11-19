from api.certificates.scan_repo import (
    _pullRepository,
    _removePulledRepo,
    getGitFameData, 
    GitFameTable, 
)
import shutil
import os

SAVE_DIR: str = "/tmp/"
TEST_REPO_URL: str = "https://github.com/whemminger/backend-ohack.dev.git"
TEST_USERNAME: str = "Squibb"

def test_pulling_removing_repo():
    memUsageBefore: shutil._ntuple_diskusage = shutil.disk_usage(SAVE_DIR)
    saveLoc: str = _pullRepository(TEST_REPO_URL)
    assert saveLoc is not "", f"Failed to pull repository \"{TEST_REPO_URL}\""
    assert os.path.exists(saveLoc), f"Path \"{saveLoc}\" does not exist"

    _removePulledRepo(saveLoc)
    assert not os.path.exists(saveLoc), f"Path \"{saveLoc}\" not removed"
    memUsageAfter: shutil._ntuple_diskusage = shutil.disk_usage(SAVE_DIR)
    assert memUsageBefore.free == memUsageAfter.free, f"Memory usage before / after does not match"


def test_scanning_reop():
    results: GitFameTable = getGitFameData(TEST_REPO_URL)
    assert results is not None, "Failed to get Gitfame table results"
    for row in results.authors:
        if (row.author == TEST_USERNAME):
            break
    else:
        assert False, f"Did not get test user \"{TEST_USERNAME}\" in table {results}"