import re
from urllib.parse import urlparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# Regular expression for email validation
# This regex follows the RFC 5322 standard for email addresses
EMAIL_REGEX = re.compile(r"""(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])""", re.IGNORECASE)

def validate_email(email):
    """
    Validate an email address.

    Args:
    email (str): The email address to validate.

    Returns:
    bool: True if the email is valid, False otherwise.
    """
    if not isinstance(email, str):
        logger.warning(f"Invalid email type: {type(email)}")
        return False
    
    if not email or len(email) > 254:
        return False

    try:
        if re.match(EMAIL_REGEX, email):
            return True
    except re.error:
        logger.exception("Regex error in email validation")
    
    return False

def validate_url(url):
    """
    Validate a URL.

    Args:
    url (str): The URL to validate.

    Returns:
    bool: True if the URL is valid, False otherwise.
    """
    if not isinstance(url, str):
        logger.warning(f"Invalid URL type: {type(url)}")
        return False

    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        logger.warning(f"Invalid URL format: {url}")
        return False

def sanitize_string(input_string, max_length=None):
    """
    Sanitize a string by trimming whitespace and optionally truncating.

    Args:
    input_string (str): The string to sanitize.
    max_length (int, optional): The maximum length of the string. If provided, 
                                the string will be truncated to this length.

    Returns:
    str: The sanitized string.
    """
    if not isinstance(input_string, str):
        logger.warning(f"Invalid input type for sanitization: {type(input_string)}")
        return ""

    sanitized = input_string.strip()
    
    if max_length is not None and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logger.info(f"String truncated to {max_length} characters")

    return sanitized

# You can add more validator functions as needed

def validate_hackathon_data(data):
    required_fields = ["title", "description", "location", "start_date", "end_date", "type", "image_url", "event_id"]
    for field in required_fields:
        if field not in data or not data[field]:
            raise ValueError(f"Missing required field: {field}")

    # Validate dates
    try:
        start_date = datetime.fromisoformat(data["start_date"])
        end_date = datetime.fromisoformat(data["end_date"])
        if end_date <= start_date:
            raise ValueError("End date must be after start date")
    except ValueError as e:
        raise ValueError(f"Invalid date format: {str(e)}")

    # Validate timezone if provided
    timezone = data.get("timezone")
    if timezone:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, KeyError):
            raise ValueError(f"Invalid timezone: {timezone}")

    # Validate constraints
    constraints = data.get("constraints", {})
    if not all(isinstance(constraints.get(k), int) for k in ["max_people_per_team", "max_teams_per_problem", "min_people_per_team"]):
        raise ValueError("Constraints must be integers")

    # Validate hacker_required_questions if present
    hacker_required_questions = constraints.get("hacker_required_questions", {})
    if hacker_required_questions:
        questions = hacker_required_questions.get("questions", [])
        if not isinstance(questions, list):
            raise ValueError("hacker_required_questions.questions must be a list")
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                raise ValueError(f"Question {i} must be an object")
            if not isinstance(q.get("question"), str) or not q.get("question"):
                raise ValueError(f"Question {i} must have a non-empty 'question' string")
            if not isinstance(q.get("required_answer"), bool):
                raise ValueError(f"Question {i} must have a boolean 'required_answer'")
            if not isinstance(q.get("error"), str) or not q.get("error"):
                raise ValueError(f"Question {i} must have a non-empty 'error' string")

    # Validate judge_venue_arrival_time if present (HH:MM 24-hour string or null)
    arrival = constraints.get("judge_venue_arrival_time")
    if arrival not in (None, ""):
        if not isinstance(arrival, str) or not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", arrival):
            raise ValueError("judge_venue_arrival_time must be HH:MM (24-hour)")

    # Validate hacker_deposit if present
    hacker_deposit = constraints.get("hacker_deposit")
    if hacker_deposit is not None:
        if not isinstance(hacker_deposit, dict):
            raise ValueError("hacker_deposit must be an object")
        if "enabled" in hacker_deposit and not isinstance(hacker_deposit["enabled"], bool):
            raise ValueError("hacker_deposit.enabled must be boolean")
        amount = hacker_deposit.get("default_amount_cents")
        if amount is not None:
            if not isinstance(amount, int) or amount < 0 or amount > 50000:
                raise ValueError("hacker_deposit.default_amount_cents must be a non-negative integer (cents) up to 50000")

    # Validate meals if present
    meals = constraints.get("meals")
    if meals is not None:
        validate_meals(meals)

    # Validate event_photos if present
    event_photos = data.get("event_photos")
    if event_photos is not None:
        validate_event_photos(event_photos)

    # Validate social_posts if present
    social_posts = data.get("social_posts")
    if social_posts is not None:
        validate_social_posts(social_posts)

    # Validate planning subobject if present
    planning = data.get("planning")
    if planning is not None:
        validate_planning_subobject(planning)


ALLOWED_DIETARY_TAGS = {
    "vegetarian",
    "vegan",
    "halal",
    "kosher",
    "gluten-free",
    "dairy-free",
    "nut-free",
    "pescatarian",
}


def validate_meals(meals):
    """Validate the constraints.meals array used for restaurant-style hacker menus."""
    if not isinstance(meals, list):
        raise ValueError("meals must be a list")
    seen_ids = set()
    for i, meal in enumerate(meals):
        if not isinstance(meal, dict):
            raise ValueError(f"meals[{i}] must be an object")
        meal_id = meal.get("id")
        if not isinstance(meal_id, str) or not meal_id:
            raise ValueError(f"meals[{i}].id must be a non-empty string")
        if meal_id in seen_ids:
            raise ValueError(f"meals[{i}].id duplicated: {meal_id}")
        seen_ids.add(meal_id)
        if not isinstance(meal.get("name"), str) or not meal.get("name"):
            raise ValueError(f"meals[{i}].name must be a non-empty string")
        if "time" in meal and meal["time"] is not None and not isinstance(meal["time"], str):
            raise ValueError(f"meals[{i}].time must be a string or null")
        if "catering_provided" in meal and not isinstance(meal["catering_provided"], bool):
            raise ValueError(f"meals[{i}].catering_provided must be a boolean")
        tags = meal.get("dietary_tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) and t in ALLOWED_DIETARY_TAGS for t in tags):
            raise ValueError(f"meals[{i}].dietary_tags must be a list from {sorted(ALLOWED_DIETARY_TAGS)}")
        items = meal.get("items", [])
        if not isinstance(items, list):
            raise ValueError(f"meals[{i}].items must be a list")
        seen_item_ids = set()
        for j, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"meals[{i}].items[{j}] must be an object")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError(f"meals[{i}].items[{j}].id must be a non-empty string")
            if item_id in seen_item_ids:
                raise ValueError(f"meals[{i}].items[{j}].id duplicated: {item_id}")
            seen_item_ids.add(item_id)
            if not isinstance(item.get("name"), str) or not item.get("name"):
                raise ValueError(f"meals[{i}].items[{j}].name must be a non-empty string")
            if "description" in item and item["description"] is not None and not isinstance(item["description"], str):
                raise ValueError(f"meals[{i}].items[{j}].description must be a string")
            item_tags = item.get("dietary_tags", [])
            if not isinstance(item_tags, list) or not all(isinstance(t, str) and t in ALLOWED_DIETARY_TAGS for t in item_tags):
                raise ValueError(f"meals[{i}].items[{j}].dietary_tags must be a list from {sorted(ALLOWED_DIETARY_TAGS)}")

MAX_EVENT_PHOTOS = 100
MAX_SOCIAL_POSTS = 25
ALLOWED_SOCIAL_PLATFORMS = {"linkedin", "instagram", "threads"}
SOCIAL_PLATFORM_HOSTS = {
    "linkedin": ("linkedin.com",),
    "instagram": ("instagram.com",),
    "threads": ("threads.net", "threads.com"),
}


def validate_event_photos(photos):
    """Validate the event_photos array stored on a hackathon."""
    if not isinstance(photos, list):
        raise ValueError("event_photos must be a list")
    if len(photos) > MAX_EVENT_PHOTOS:
        raise ValueError(f"event_photos may not exceed {MAX_EVENT_PHOTOS} entries")
    for i, photo in enumerate(photos):
        if not isinstance(photo, dict):
            raise ValueError(f"event_photos[{i}] must be an object")
        url = photo.get("url")
        if not isinstance(url, str) or not validate_url(url):
            raise ValueError(f"event_photos[{i}].url must be a valid URL")
        for optional in ("caption", "credit"):
            val = photo.get(optional)
            if val is not None and not isinstance(val, str):
                raise ValueError(f"event_photos[{i}].{optional} must be a string")
        sort_order = photo.get("sort_order")
        if sort_order is not None and not isinstance(sort_order, int):
            raise ValueError(f"event_photos[{i}].sort_order must be an integer")


def validate_social_posts(posts):
    """Validate the social_posts array stored on a hackathon."""
    if not isinstance(posts, list):
        raise ValueError("social_posts must be a list")
    if len(posts) > MAX_SOCIAL_POSTS:
        raise ValueError(f"social_posts may not exceed {MAX_SOCIAL_POSTS} entries")
    for i, post in enumerate(posts):
        if not isinstance(post, dict):
            raise ValueError(f"social_posts[{i}] must be an object")
        platform = post.get("platform")
        if platform not in ALLOWED_SOCIAL_PLATFORMS:
            raise ValueError(
                f"social_posts[{i}].platform must be one of {sorted(ALLOWED_SOCIAL_PLATFORMS)}"
            )
        url = post.get("url")
        if not isinstance(url, str) or not validate_url(url):
            raise ValueError(f"social_posts[{i}].url must be a valid URL")
        host = (urlparse(url).netloc or "").lower()
        host = host[4:] if host.startswith("www.") else host
        allowed_hosts = SOCIAL_PLATFORM_HOSTS[platform]
        if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
            raise ValueError(
                f"social_posts[{i}].url host must match platform '{platform}' ({allowed_hosts})"
            )
        caption = post.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise ValueError(f"social_posts[{i}].caption must be a string")


_SLACK_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def validate_planning_subobject(planning):
    """Validate the per-hackathon `planning` subobject persisted on a hackathon doc."""
    if not isinstance(planning, dict):
        raise ValueError("planning must be an object")

    enabled = planning.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("planning.enabled must be a boolean")

    editors = planning.get("editors", [])
    if not isinstance(editors, list):
        raise ValueError("planning.editors must be a list of PropelAuth user IDs")
    if len(editors) > 200:
        raise ValueError("planning.editors may not exceed 200 entries")
    seen_editors = set()
    for i, ed in enumerate(editors):
        if not isinstance(ed, str) or not ed.strip():
            raise ValueError(f"planning.editors[{i}] must be a non-empty string")
        if ed in seen_editors:
            raise ValueError(f"planning.editors[{i}] duplicated: {ed}")
        seen_editors.add(ed)

    slack = planning.get("slack")
    if slack is not None:
        if not isinstance(slack, dict):
            raise ValueError("planning.slack must be an object")
        channel = slack.get("channel", "")
        if channel:
            if not isinstance(channel, str):
                raise ValueError("planning.slack.channel must be a string")
            normalized = channel.lstrip("#").strip()
            if normalized and not _SLACK_CHANNEL_RE.match(normalized):
                raise ValueError(
                    "planning.slack.channel must be lowercase letters, digits, hyphens or underscores (max 80 chars)"
                )
        notify = slack.get("notify_on_card_change", False)
        if not isinstance(notify, bool):
            raise ValueError("planning.slack.notify_on_card_change must be a boolean")

    seeded = planning.get("template_seeded", False)
    if not isinstance(seeded, bool):
        raise ValueError("planning.template_seeded must be a boolean")

    budget_widget = planning.get("budget_widget_on_event_page", False)
    if not isinstance(budget_widget, bool):
        raise ValueError("planning.budget_widget_on_event_page must be a boolean")


if __name__ == "__main__":
    # Simple tests
    print(validate_email("test@example.com"))  # Should print True
    print(validate_email("invalid-email"))  # Should print False
    print(validate_url("https://www.example.com"))  # Should print True
    print(validate_url("invalid-url"))  # Should print False
    print(sanitize_string("  Hello, World!  "))  # Should print "Hello, World!"
    print(sanitize_string("Too long", 5))  # Should print "Too l"