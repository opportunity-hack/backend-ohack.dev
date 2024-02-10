import pytest

def pytest_configure():
    pytest.CERTIFICATE_TEST_REPO_URL: str = "https://github.com/whemminger/backend-ohack.dev.git"
    pytest.CERTIFICATE_TEST_USERNAME: str = "Squibb"
    pytest.CERTIFICATE_SAVE_DIR: str = "/tmp/"