"""Vanity profile slugs (portfolio URLs like ohack.dev/u/<slug>).

Slugs live in the `user_slugs` collection where the slug IS the document id —
uniqueness is enforced atomically by DocumentReference.create(). Old slugs stay
behind as aliases (is_primary=False) so shared links never break and nobody can
claim a slug you previously used (prevents slug-jacking).
"""
import re
from datetime import datetime, timedelta

from common.log import get_logger, info, warning

logger = get_logger("user_slug_service")

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])?$")
# users.id values are uuid.uuid1().hex — a slug must never shadow one
DB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

MAX_SLUGS_PER_USER = 5
SLUG_CHANGE_COOLDOWN_HOURS = 24

RESERVED_SLUGS = frozenset({
    "about", "admin", "api", "app", "blog", "cert", "certs", "certificates",
    "community", "community-champions", "contact", "cdn", "dev", "docs",
    "donate", "edit", "faq", "feedback", "giveaway", "hack", "hackathon",
    "hackathons", "hacker", "hackers", "help", "hearts", "home", "index",
    "internships", "jobs", "join", "judge", "judges", "leaderboard", "legal",
    "letters", "login", "logout", "me", "media", "mentor", "mentors",
    "myfeedback", "myprofile", "new", "news", "nonprofit", "nonprofits",
    "null", "office-hours", "ohack", "onboarding", "opportunity-hack",
    "portfolio", "praise", "praises", "privacy", "profile", "profiles",
    "project", "projects", "search", "settings", "signup", "sitemap",
    "slack", "sponsor", "sponsors", "staff", "static", "store", "support",
    "team", "teams", "terms", "test", "undefined", "user", "users", "u",
    "volunteer", "volunteers", "www",
})


def normalize_slug(slug):
    return (slug or "").strip().lower()


def validate_slug(slug):
    """Returns (is_valid, reason). `slug` must already be normalized."""
    if not slug:
        return False, "Slug is required"
    if len(slug) < 3 or len(slug) > 30:
        return False, "Slug must be 3-30 characters"
    if not SLUG_PATTERN.match(slug):
        return False, "Use lowercase letters, numbers, and hyphens (no leading/trailing hyphen)"
    if slug in RESERVED_SLUGS:
        return False, "This name is reserved"
    if DB_ID_PATTERN.match(slug):
        return False, "This name is reserved"
    return True, None


def check_slug_availability(slug):
    """Availability check for the editor's live validation."""
    from db.db import fetch_user_db_id_by_slug

    normalized = normalize_slug(slug)
    valid, reason = validate_slug(normalized)
    if not valid:
        return {"slug": normalized, "valid": False, "available": False, "reason": reason}

    existing = fetch_user_db_id_by_slug(normalized)
    if existing is not None:
        return {"slug": normalized, "valid": True, "available": False, "reason": "Already taken"}
    return {"slug": normalized, "valid": True, "available": True, "reason": None}


def claim_profile_slug(propel_id, slug):
    """Claim (or change to) `slug` for the authenticated user.

    Returns (payload, http_status). Old slug is kept as an alias.
    """
    from db.db import create_user_slug, fetch_user_db_id_by_slug, fetch_user_slugs_by_db_id
    from services.users_service import _resolve_and_ensure_user, clear_portfolio_caches

    normalized = normalize_slug(slug)
    valid, reason = validate_slug(normalized)
    if not valid:
        return {"error": reason}, 400

    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"error": "Could not resolve your account"}, 404

    existing = fetch_user_db_id_by_slug(normalized)
    if existing is not None and existing.get("user_db_id") != user.id:
        return {"error": "Already taken"}, 409

    my_slugs = fetch_user_slugs_by_db_id(user.id)
    previous_primary = next((s.get("slug") for s in my_slugs if s.get("is_primary")), None)

    if previous_primary == normalized:
        return {"slug": normalized, "previous_slug": None, "message": "Already your URL"}, 200

    if previous_primary is not None:
        # Rename path: throttle to one change per 24h, cap total aliases.
        owned_slugs = {s.get("slug") for s in my_slugs}
        if normalized not in owned_slugs and len(my_slugs) >= MAX_SLUGS_PER_USER:
            return {"error": f"You can hold at most {MAX_SLUGS_PER_USER} URLs (old ones stay as aliases)"}, 400
        newest = max((s.get("created_at") or "" for s in my_slugs), default="")
        if newest:
            try:
                newest_dt = datetime.fromisoformat(newest.replace("Z", ""))
                if datetime.now() - newest_dt < timedelta(hours=SLUG_CHANGE_COOLDOWN_HOURS):
                    return {"error": "You can change your URL once every 24 hours"}, 429
            except ValueError:
                pass

    created = create_user_slug(normalized, user.id, previous_slug=previous_primary)
    if not created:
        return {"error": "Already taken"}, 409

    info(logger, "Slug claimed", slug=normalized, user_db_id=user.id, previous=previous_primary)
    try:
        clear_portfolio_caches(user.id)
    except Exception as e:
        warning(logger, "Failed to clear portfolio caches after slug claim", error=str(e))

    return {"slug": normalized, "previous_slug": previous_primary}, 200
