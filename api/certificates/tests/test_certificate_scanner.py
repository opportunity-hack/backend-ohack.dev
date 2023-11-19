from api.certificates.scan_repo import (
    _pullRepository,
    _removePulledRepo,
    getGitFameData, 
    GitFameTable, 
)
import pytest
import shutil
import os


def test_pulling_removing_repo():
    memUsageBefore: shutil._ntuple_diskusage = shutil.disk_usage(pytest.CERTIFICATE_SAVE_DIR)
    saveLoc: str = _pullRepository(pytest.CERTIFICATE_TEST_REPO_URL)
    assert saveLoc is not "", f"Failed to pull repository \"{pytest.CERTIFICATE_TEST_REPO_URL}\""
    assert os.path.exists(saveLoc), f"Path \"{saveLoc}\" does not exist"

    _removePulledRepo(saveLoc)
    assert not os.path.exists(saveLoc), f"Path \"{saveLoc}\" not removed"
    memUsageAfter: shutil._ntuple_diskusage = shutil.disk_usage(pytest.CERTIFICATE_SAVE_DIR)
    assert memUsageBefore.free == memUsageAfter.free, f"Memory usage before / after does not match"


def test_scanning_reop():
    results: GitFameTable = getGitFameData(pytest.CERTIFICATE_TEST_REPO_URL)
    assert results is not None, "Failed to get Gitfame table results"
    for row in results.authors:
        if (row.author == pytest.CERTIFICATE_TEST_USERNAME):
            break
    else:
        assert False, f"Did not get test user \"{pytest.CERTIFICATE_TEST_USERNAME}\" in table {results}"