import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# Pre-mock all problematic modules before any imports
_mock_modules = [
    "db.db", "db.firestore", "db.mem", "db.interface",
    "google.cloud", "google.cloud.storage", "google.cloud.firestore",
    "common.utils.firebase", "common.utils.slack", "common.utils.cdn",
    "openai", "PIL", "PIL.ImageFont", "PIL.ImageDraw", "PIL.Image",
    "PIL.ImageEnhance",
]
for mod_name in _mock_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Now safe to import
from services.hearts_service import get_hearts_leaderboard


def _make_user(name, user_id, profile_image, history):
    user = MagicMock()
    user.name = name
    user.id = user_id
    user.profile_image = profile_image
    user.history = history
    return user


USERS = [
    _make_user("Alice", "u1", "img1.png", {
        "how": {"code_reliability": 3, "standups_completed": 2},
        "what": {"documentation": 1},
    }),
    _make_user("Bob", "u2", "img2.png", {
        "how": {"code_reliability": 1},
        "what": {"documentation": 0.5},
    }),
    _make_user("Carol", "u3", "img3.png", {
        "how": {"code_reliability": 0},
        "what": {"documentation": 0},
    }),
    _make_user("Dave", "u4", "img4.png", {
        "how": {"code_reliability": 10},
        "what": {"unit_test_writing": 5},
        "certificates": ["cert1.png"],
    }),
    _make_user("Eve", "u5", None, {}),
]


@patch("services.hearts_service.fetch_users", return_value=USERS)
def test_sort_order_descending(mock_fetch):
    result = get_hearts_leaderboard(limit=50)
    hearts = [entry["totalHearts"] for entry in result]
    assert hearts == sorted(hearts, reverse=True)


@patch("services.hearts_service.fetch_users", return_value=USERS)
def test_zero_hearts_excluded(mock_fetch):
    result = get_hearts_leaderboard(limit=50)
    names = [entry["name"] for entry in result]
    assert "Carol" not in names
    assert "Eve" not in names


@patch("services.hearts_service.fetch_users", return_value=USERS)
def test_certificates_excluded_from_count(mock_fetch):
    result = get_hearts_leaderboard(limit=50)
    dave = next(e for e in result if e["name"] == "Dave")
    # Only how + what should count: 10 + 5 = 15, not certificates
    assert dave["totalHearts"] == 15


@patch("services.hearts_service.fetch_users", return_value=USERS)
def test_limit_respected(mock_fetch):
    result = get_hearts_leaderboard(limit=2)
    assert len(result) == 2


@patch("services.hearts_service.fetch_users", return_value=USERS)
def test_correct_return_fields(mock_fetch):
    result = get_hearts_leaderboard(limit=50)
    expected_keys = {"name", "totalHearts", "userId", "profileImage"}
    for entry in result:
        assert set(entry.keys()) == expected_keys
