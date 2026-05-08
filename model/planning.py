"""Schema constants and defaults for the per-hackathon planning board."""

PLANNING_FIELD = "planning"

ALLOWED_BUDGET_BUCKETS = {"food", "prize", "swag"}
ALLOWED_BUDGET_STATES = {"estimated", "committed", "paid"}
ALLOWED_CARD_KINDS = {
    "freetext",
    "judges",
    "mentors",
    "hackers",
    "nonprofits",
    "teams",
    "sponsor_prospect",
}
ALLOWED_OUTREACH_STATUSES = {
    "prospect",
    "contacted",
    "in-discussion",
    "committed",
    "declined",
    "no-response",
}
ALLOWED_SPONSOR_TIERS = {"visionary", "transformer", "changemaker", "in-kind", "tbd"}

MAX_PLANNING_LISTS = 50
MAX_PLANNING_CARDS_PER_LIST = 200
MAX_PLANNING_LABELS = 50
MAX_COMMENT_LEN = 4000
MAX_ATTACHMENTS_PER_CARD = 20
MAX_CARD_TITLE_LEN = 200
MAX_CARD_DESCRIPTION_LEN = 20_000
MAX_CHECKLISTS_PER_CARD = 20
MAX_CHECKLIST_ITEMS = 100
MAX_BUDGET_CENTS = 1_000_000_00  # $1M sanity cap
MAX_COMMENTS_PER_USER_PER_MIN = 10


def default_planning_subobject():
    """Default shape for the per-hackathon planning subobject."""
    return {
        "enabled": False,
        "editors": [],
        "slack": {
            "channel": "",
            "notify_on_card_change": False,
        },
        "template_seeded": False,
        "budget_widget_on_event_page": False,
    }
