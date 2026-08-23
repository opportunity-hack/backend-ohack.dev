"""Volunteer job board: public listings + applications.

Collections:
  job_listings     — doc id = slug (immutable after create). Admin CRUD via
                     /admin/jobs on the frontend; public pages at /jobs.
  job_applications — doc id = uuid4. Login-required applications with a work
                     sample, resume (CDN PDF), and required intro video.

Applications trigger a confirmation email to the applicant (with a built-in
"reply within 5 days" responsiveness test) and an FYI email to
questions@ohack.org. Admin decisions send a warm templated email.
"""

import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import pytz
import resend
from cachetools import cached, TTLCache
from ratelimiter import RateLimiter

from db.db import get_db
from google.cloud import firestore
from common.log import get_logger
from common.utils.slack import send_slack_audit
from common.utils.validators import (
    ALLOWED_JOB_APPLICATION_STATUSES,
    sanitize_string,
    validate_job_application,
    validate_job_listing,
    validate_job_listing_partial,
)
from services.volunteers_service import verify_recaptcha

logger = get_logger("services.jobs_service")

LISTINGS_COLLECTION = "job_listings"
APPLICATIONS_COLLECTION = "job_applications"

ALLOWED_RESUME_CONTENT_TYPES = {"application/pdf": "pdf"}
MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB

JOBS_FYI_EMAIL = "questions@ohack.org"
ADMIN_APPLICATIONS_URL = "https://www.ohack.dev/admin/jobs?tab=applications"

PUBLIC_LISTING_LIST_FIELDS = (
    "slug", "title", "status", "location_type", "location_label",
    "hours_per_week_label", "min_hours_per_week", "duration_ask",
    "summary", "posted_at", "valid_through",
)
PUBLIC_LISTING_DETAIL_FIELDS = PUBLIC_LISTING_LIST_FIELDS + (
    "description_markdown", "work_sample_prompt", "video_prompts",
)

APPLICATION_ADMIN_PATCH_KEYS = ("status", "admin_notes")


def _notifications_disabled() -> bool:
    """Mirror of volunteers_service._notifications_disabled — suppress real
    Resend/Slack sends when unit tests run against MockFirestore."""
    return os.environ.get("ENVIRONMENT") == "test"


def _now_iso() -> str:
    az_timezone = pytz.timezone("US/Arizona")
    return datetime.now(az_timezone).isoformat()


def _cdn_server() -> str:
    return os.getenv("CDN_SERVER", "https://cdn.ohack.dev").rstrip("/")


def _project(doc: Dict[str, Any], fields) -> Dict[str, Any]:
    return {k: doc.get(k) for k in fields}


# ---------------------------------------------------------------------------
# Public listings
# ---------------------------------------------------------------------------

@cached(cache=TTLCache(maxsize=1, ttl=300), lock=threading.Lock())
def get_public_listings():
    """Published + closed listings, newest first. Draft/hidden never leak."""
    db = get_db()
    results = []
    for doc in db.collection(LISTINGS_COLLECTION).stream():
        doc_dict = doc.to_dict() or {}
        doc_dict["slug"] = doc.id
        if doc_dict.get("status") in ("published", "closed"):
            results.append(_project(doc_dict, PUBLIC_LISTING_LIST_FIELDS))
    results.sort(key=lambda item: item.get("posted_at") or "", reverse=True)
    return results


@cached(cache=TTLCache(maxsize=50, ttl=300), lock=threading.Lock())
def get_public_listing(slug: str) -> Optional[Dict[str, Any]]:
    """Full public doc for one listing; None for draft/hidden/unknown.
    Closed listings still resolve so shared links render a calm closed state."""
    db = get_db()
    snap = db.collection(LISTINGS_COLLECTION).document(slug).get()
    if snap is None or not snap.exists:
        return None
    doc_dict = snap.to_dict() or {}
    doc_dict["slug"] = snap.id
    if doc_dict.get("status") not in ("published", "closed"):
        return None
    return _project(doc_dict, PUBLIC_LISTING_DETAIL_FIELDS)


def _clear_listing_caches():
    get_public_listings.cache_clear()
    get_public_listing.cache_clear()


# ---------------------------------------------------------------------------
# Admin: listings CRUD
# ---------------------------------------------------------------------------

def admin_list_listings():
    db = get_db()
    results = []
    for doc in db.collection(LISTINGS_COLLECTION).stream():
        doc_dict = doc.to_dict() or {}
        doc_dict["slug"] = doc.id
        results.append(doc_dict)
    results.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"success": True, "listings": results}, 200


def admin_create_listing(json_in: Optional[Dict[str, Any]], actor: Optional[Dict[str, Any]]):
    data = json_in or {}
    try:
        validate_job_listing(data)
    except ValueError as e:
        return {"success": False, "error": str(e)}, 400

    slug = data["slug"]
    db = get_db()
    doc_ref = db.collection(LISTINGS_COLLECTION).document(slug)
    if doc_ref.get().exists:
        return {"success": False, "error": f"A listing with slug '{slug}' already exists"}, 409

    now = _now_iso()
    doc = {
        "title": data["title"],
        "status": data.get("status", "draft"),
        "location_type": data.get("location_type", "remote"),
        "location_label": data.get("location_label", ""),
        "hours_per_week_label": data.get("hours_per_week_label", ""),
        "min_hours_per_week": data.get("min_hours_per_week", 0),
        "duration_ask": data.get("duration_ask", ""),
        "summary": data.get("summary", ""),
        "description_markdown": data.get("description_markdown", ""),
        "work_sample_prompt": data.get("work_sample_prompt", ""),
        "video_prompts": data.get("video_prompts", []),
        "valid_through": data.get("valid_through", ""),
        "posted_at": now if data.get("status") == "published" else "",
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "last_updated_by": actor,
    }
    doc_ref.set(doc)
    _clear_listing_caches()
    send_slack_audit(action="job_listing_create", message=f"Job listing '{slug}' created")
    doc["slug"] = slug
    return {"success": True, "listing": doc}, 201


def admin_update_listing(slug: str, json_in: Optional[Dict[str, Any]], actor: Optional[Dict[str, Any]]):
    db = get_db()
    doc_ref = db.collection(LISTINGS_COLLECTION).document(slug)
    snap = doc_ref.get()
    if not snap.exists:
        return {"success": False, "error": "Listing not found"}, 404
    existing = snap.to_dict() or {}

    cleaned, skipped = validate_job_listing_partial(json_in)
    if not cleaned:
        return {"success": False, "error": "No editable fields in payload", "skipped": skipped}, 400

    if cleaned.get("status") == "published" and not existing.get("posted_at"):
        cleaned["posted_at"] = _now_iso()
    cleaned["updated_at"] = _now_iso()
    cleaned["last_updated_by"] = actor

    doc_ref.update(cleaned)
    _clear_listing_caches()
    send_slack_audit(action="job_listing_update", message=f"Job listing '{slug}' updated: {sorted(cleaned)}")
    return {"success": True, "skipped": skipped}, 200


def admin_delete_listing(slug: str):
    db = get_db()
    doc_ref = db.collection(LISTINGS_COLLECTION).document(slug)
    if not doc_ref.get().exists:
        return {"success": False, "error": "Listing not found"}, 404
    doc_ref.delete()
    _clear_listing_caches()
    send_slack_audit(action="job_listing_delete", message=f"Job listing '{slug}' deleted")
    return {"success": True}, 200


# ---------------------------------------------------------------------------
# Applicant: resume upload + submit
# ---------------------------------------------------------------------------

def create_resume_upload_url(propel_id: str, content_type: Optional[str], content_length):
    """Mint a signed GCS PUT URL for a resume PDF. Returns (payload, status)."""
    if content_type not in ALLOWED_RESUME_CONTENT_TYPES:
        return {"error": "Resumes must be PDF files"}, 400
    try:
        content_length = int(content_length)
    except (TypeError, ValueError):
        return {"error": "content_length is required"}, 400
    if content_length <= 0 or content_length > MAX_RESUME_BYTES:
        return {"error": f"Resume must be under {MAX_RESUME_BYTES // (1024 * 1024)}MB"}, 400

    from services.users_service import _resolve_and_ensure_user
    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"error": "Could not resolve your account"}, 404

    from common.utils.cdn import generate_signed_upload_url
    ext = ALLOWED_RESUME_CONTENT_TYPES[content_type]
    filename = f"resume_{uuid.uuid4().hex}.{ext}"
    try:
        payload = generate_signed_upload_url(
            directory=f"job_applications/{user.id}",
            filename=filename,
            content_type=content_type,
            max_bytes=MAX_RESUME_BYTES,
        )
    except Exception as e:
        logger.exception(f"Failed to generate signed resume upload URL: {e}")
        return {"error": "Could not create an upload URL"}, 500
    return payload, 200


def _verify_resume_url(url: str, db_id: str) -> Optional[str]:
    """Returns an error string when the resume URL isn't a verified own upload."""
    cdn_prefix = f"{_cdn_server()}/job_applications/{db_id}/"
    if not url.startswith(cdn_prefix):
        return "resume_url must be a resume uploaded through this form"
    blob_path = url[len(_cdn_server()) + 1:]
    try:
        from common.utils.cdn import get_blob_metadata
        meta = get_blob_metadata(blob_path)
    except Exception as e:
        logger.exception(f"Failed to verify uploaded resume: {e}")
        return "Could not verify the uploaded resume"
    if not meta.get("exists"):
        return "Resume upload not found — did the upload finish?"
    if meta.get("content_type") not in ALLOWED_RESUME_CONTENT_TYPES:
        return "Uploaded resume is not a PDF"
    if (meta.get("size") or 0) > MAX_RESUME_BYTES:
        return "Uploaded resume exceeds the size limit"
    return None


def _verify_video_url(url: str, db_id: str) -> Optional[str]:
    """Returns an error string unless the video is an own-CDN upload or an
    allowlisted provider link (mirrors the bio-video rules)."""
    from services.users_service import ALLOWED_VIDEO_CONTENT_TYPES, ALLOWED_VIDEO_LINK_HOSTS

    cdn_prefix = f"{_cdn_server()}/users/{db_id}/"
    if url.startswith(cdn_prefix):
        blob_path = url[len(_cdn_server()) + 1:]
        try:
            from common.utils.cdn import get_blob_metadata
            meta = get_blob_metadata(blob_path)
        except Exception as e:
            logger.exception(f"Failed to verify uploaded video: {e}")
            return "Could not verify the uploaded video"
        if not meta.get("exists"):
            return "Video upload not found — did the upload finish?"
        if meta.get("content_type") not in ALLOWED_VIDEO_CONTENT_TYPES:
            return "Uploaded file is not an allowed video type"
        return None

    host = (urlparse(url).netloc or "").lower().split(":")[0]
    if host not in ALLOWED_VIDEO_LINK_HOSTS:
        return "Video links must be YouTube, Vimeo, or Loom (or an upload)"
    return None


def _find_application(db, slug: str, propel_id: str):
    docs = db.collection(APPLICATIONS_COLLECTION) \
        .where("listing_slug", "==", slug) \
        .where("user_id", "==", propel_id) \
        .limit(1).stream()
    for doc in docs:
        return doc
    return None


@RateLimiter(max_calls=10, period=60)
def submit_application(propel_id: str, ip_address: Optional[str], slug: str,
                       data: Optional[Dict[str, Any]]):
    """Create a job application. Returns (payload, status)."""
    data = data or {}

    listing = get_public_listing(slug)
    if listing is None:
        return {"success": False, "error": "Listing not found"}, 404
    if listing.get("status") != "published":
        return {"success": False, "error": "This role is no longer accepting applications"}, 409

    recaptcha_token = data.get("recaptchaToken")
    if not verify_recaptcha(recaptcha_token) and os.environ.get("FLASK_ENV") != "development":
        return {"success": False, "error": "reCAPTCHA verification failed"}, 400

    try:
        validate_job_application(data)
    except ValueError as e:
        return {"success": False, "error": str(e)}, 400

    from services.users_service import _resolve_and_ensure_user
    user, _user_id = _resolve_and_ensure_user(propel_id)
    if user is None or not getattr(user, "id", None):
        return {"success": False, "error": "Could not resolve your account"}, 404

    resume_error = _verify_resume_url(data["resume_url"], user.id)
    if resume_error:
        return {"success": False, "error": resume_error}, 400
    video_error = _verify_video_url(data["video_url"], user.id)
    if video_error:
        return {"success": False, "error": video_error}, 400

    db = get_db()
    if _find_application(db, slug, propel_id) is not None:
        return {"success": False,
                "error": "You've already applied for this role — check your email for next steps"}, 409

    now = _now_iso()
    application_id = str(uuid.uuid4())
    application = {
        "listing_slug": slug,
        "listing_title": listing.get("title", ""),
        "user_id": propel_id,
        "db_id": user.id,
        "name": sanitize_string(data.get("name"), 200),
        "email": sanitize_string(data.get("email"), 200),
        "pronouns": sanitize_string(data.get("pronouns") or "", 100),
        "phone": sanitize_string(data.get("phone") or "", 50),
        "location": sanitize_string(data.get("location") or "", 200),
        "linkedin_url": sanitize_string(data.get("linkedin_url"), 400),
        "resume_url": data["resume_url"],
        "video_url": data["video_url"],
        "hours_per_week": sanitize_string(data.get("hours_per_week"), 50),
        "duration_commitment": sanitize_string(data.get("duration_commitment"), 100),
        "preferred_channel": sanitize_string(data.get("preferred_channel") or "", 50),
        "slack_member": sanitize_string(data.get("slack_member") or "", 50),
        "in_person_ok": bool(data.get("in_person_ok", False)),
        "visa_ack": True,
        "work_sample_answer": data["work_sample_answer"].strip(),
        "why_ohack": sanitize_string(data.get("why_ohack") or "", 2000),
        "referral_source": sanitize_string(data.get("referral_source") or "", 200),
        "status": "submitted",
        "status_history": [{"status": "submitted", "at": now, "by": "applicant"}],
        "admin_notes": "",
        "sent_emails": [],
        "timestamp": now,
        "ip_address": ip_address or "",
    }
    db.collection(APPLICATIONS_COLLECTION).document(application_id).set(application)
    logger.info(f"Job application {application_id} created for '{slug}'")

    try:
        _send_applicant_confirmation_email(application_id, application, listing)
    except Exception as e:
        logger.exception(f"Job application confirmation email failed: {e}")
    try:
        _send_admin_fyi_email(application_id, application)
    except Exception as e:
        logger.exception(f"Job application FYI email failed: {e}")

    send_slack_audit(action="job_application",
                     message=f"New application for '{listing.get('title', slug)}' from {application['email']}")
    return {"success": True, "application_id": application_id}, 201


def get_my_application(propel_id: str, slug: str):
    db = get_db()
    doc = _find_application(db, slug, propel_id)
    if doc is None:
        return {"applied": False}, 200
    doc_dict = doc.to_dict() or {}
    return {"applied": True,
            "status": doc_dict.get("status", "submitted"),
            "timestamp": doc_dict.get("timestamp", "")}, 200


# ---------------------------------------------------------------------------
# Admin: applications
# ---------------------------------------------------------------------------

def admin_list_applications(listing_slug: Optional[str] = None):
    db = get_db()
    query = db.collection(APPLICATIONS_COLLECTION)
    if listing_slug:
        query = query.where("listing_slug", "==", listing_slug)
    results = []
    for doc in query.stream():
        doc_dict = doc.to_dict() or {}
        doc_dict["id"] = doc.id
        results.append(doc_dict)
    results.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return {"success": True, "applications": results}, 200


def admin_update_application(application_id: str, json_in: Optional[Dict[str, Any]],
                             actor: Optional[Dict[str, Any]]):
    db = get_db()
    doc_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    snap = doc_ref.get()
    if not snap.exists:
        return {"success": False, "error": "Application not found"}, 404
    existing = snap.to_dict() or {}

    patch = {k: v for k, v in (json_in or {}).items() if k in APPLICATION_ADMIN_PATCH_KEYS}
    if not patch:
        return {"success": False, "error": "No editable fields in payload"}, 400

    new_status = patch.get("status")
    if new_status is not None:
        if new_status not in ALLOWED_JOB_APPLICATION_STATUSES:
            return {"success": False,
                    "error": f"status must be one of {list(ALLOWED_JOB_APPLICATION_STATUSES)}"}, 400
        if new_status != existing.get("status"):
            actor_email = (actor or {}).get("email") or "admin"
            patch["status_history"] = firestore.ArrayUnion(
                [{"status": new_status, "at": _now_iso(), "by": actor_email}]
            )

    if "admin_notes" in patch and not isinstance(patch["admin_notes"], str):
        return {"success": False, "error": "admin_notes must be a string"}, 400

    doc_ref.update(patch)
    send_slack_audit(action="job_application_update",
                     message=f"Application {application_id} updated: {sorted(patch)}")
    return {"success": True}, 200


def admin_decide_application(application_id: str, json_in: Optional[Dict[str, Any]],
                             actor: Optional[Dict[str, Any]]):
    """Accept or (kindly) reject an application and send the matching email."""
    data = json_in or {}
    decision = data.get("decision")
    if decision not in ("accepted", "rejected"):
        return {"success": False, "error": "decision must be 'accepted' or 'rejected'"}, 400
    personal_note = sanitize_string(data.get("personal_note") or "", 2000)

    db = get_db()
    doc_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    snap = doc_ref.get()
    if not snap.exists:
        return {"success": False, "error": "Application not found"}, 404
    application = snap.to_dict() or {}

    resend_id = None
    try:
        resend_id = _send_decision_email(application, decision, personal_note)
    except Exception as e:
        logger.exception(f"Decision email failed for application {application_id}: {e}")

    actor_email = (actor or {}).get("email") or "admin"
    patch = {
        "status": decision,
        "status_history": firestore.ArrayUnion(
            [{"status": decision, "at": _now_iso(), "by": actor_email}]
        ),
    }
    if resend_id:
        patch["sent_emails"] = firestore.ArrayUnion([{
            "resend_id": resend_id,
            "subject": _decision_subject(application, decision),
            "timestamp": _now_iso(),
            "sent_by": actor_email,
            "recipient_type": f"decision_{decision}",
        }])
    doc_ref.update(patch)

    send_slack_audit(action="job_application_decision",
                     message=f"Application {application_id} ({application.get('email', '?')}) marked {decision}")
    return {"success": True, "email_sent": bool(resend_id)}, 200


# ---------------------------------------------------------------------------
# Emails (Resend; house style from volunteers_service)
# ---------------------------------------------------------------------------

_EMAIL_SHELL_TOP = """
<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border-radius: 5px;">
    <div style="text-align: center; margin-bottom: 20px;">
        <img src="https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Dark_Blue.png" alt="Opportunity Hack Logo" style="max-width: 150px;">
    </div>
"""

_EMAIL_SHELL_BOTTOM = """
    <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
        <p style="font-size: 14px; color: #777;">The Opportunity Hack Team</p>
        <p style="font-size: 14px; color: #777;">Website: <a href="https://www.ohack.dev" style="color: #3498db;">ohack.dev</a></p>
    </div>
</div>
"""


def _resend_ready() -> bool:
    if _notifications_disabled():
        logger.info("ENVIRONMENT=test — skipping job email send")
        return False
    resend_api_key = os.environ.get("RESEND_WELCOME_EMAIL_KEY")
    if not resend_api_key:
        logger.error("Missing required environment variable RESEND_WELCOME_EMAIL_KEY")
        return False
    resend.api_key = resend_api_key
    return True


def _send_and_get_id(params) -> Optional[str]:
    email_result = resend.Emails.send(params)
    if isinstance(email_result, dict):
        return email_result.get("id")
    return getattr(email_result, "id", None)


def _send_applicant_confirmation_email(application_id: str, application: Dict[str, Any],
                                       listing: Dict[str, Any]) -> Optional[str]:
    if not _resend_ready():
        return None

    name = application.get("name") or "there"
    title = listing.get("title", "volunteer role")
    subject = f"[Application received] {title} — Opportunity Hack"
    html = f"""{_EMAIL_SHELL_TOP}
    <h2 style="color: #3498db; text-align: center;">We got your application!</h2>
    <p style="font-size: 16px;">Hi {name},</p>
    <p style="font-size: 16px;">Thanks for applying to be our <strong>{title}</strong>. We know this application
       took real effort — the work sample and video are how we find people who care, and we appreciate you
       putting in the time.</p>
    <div style="background-color: #fff8e1; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
        <h3 style="color: #92400e; margin: 0 0 8px 0; font-size: 18px;">✉️ One more step — reply to confirm</h3>
        <p style="margin: 0; font-size: 15px; color: #78350f;">
            <strong>Reply to this email within 5 days</strong> (or join our
            <a href="https://www.ohack.dev/signup" style="color: #92400e;">Slack</a> and DM us) to confirm your
            application is active. This role runs on fast, clear communication — consider this the first task.
        </p>
    </div>
    <p style="font-size: 16px;"><strong>What happens next:</strong> we review every application by hand
       (typically within a week), then reach out from questions@ohack.org to set up a short call.</p>
    <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #3498db; margin: 15px 0;">
        <p style="margin: 0; font-size: 14px; color: #34495e;">A reminder: this is a volunteer role with a
           nonprofit — it is unpaid, and we are unable to sponsor visas. What you get is real, portfolio-worthy
           experience, Hearts toward certificates, and LinkedIn recommendations &amp; references from work
           that actually shipped.</p>
    </div>
{_EMAIL_SHELL_BOTTOM}"""

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": [application["email"]],
        "reply_to": JOBS_FYI_EMAIL,
        "subject": subject,
        "html": html,
    }
    resend_id = _send_and_get_id(params)
    logger.info(f"Sent job application confirmation to {application['email']} (resend_id={resend_id})")
    if resend_id:
        try:
            get_db().collection(APPLICATIONS_COLLECTION).document(application_id).update({
                "sent_emails": firestore.ArrayUnion([{
                    "resend_id": resend_id,
                    "subject": subject,
                    "timestamp": _now_iso(),
                    "sent_by": "system",
                    "recipient_type": "application_confirmation",
                }])
            })
        except Exception as e:
            logger.warning(f"Failed to track confirmation email for {application_id}: {e}")
    return resend_id


def _send_admin_fyi_email(application_id: str, application: Dict[str, Any]) -> bool:
    if not _resend_ready():
        return False

    def _row(label, value):
        return (f'<tr><td style="padding: 4px 12px 4px 0; color: #777; vertical-align: top;">{label}</td>'
                f'<td style="padding: 4px 0;">{value}</td></tr>')

    def _link(url, text=None):
        return f'<a href="{url}" style="color: #3498db;">{text or url}</a>' if url else "—"

    rows = "".join([
        _row("Name", application.get("name", "—")),
        _row("Email", application.get("email", "—")),
        _row("Pronouns", application.get("pronouns") or "—"),
        _row("Phone", application.get("phone") or "—"),
        _row("Location", application.get("location") or "—"),
        _row("LinkedIn", _link(application.get("linkedin_url"))),
        _row("Resume", _link(application.get("resume_url"), "View resume (PDF)")),
        _row("Video", _link(application.get("video_url"), "Watch intro video")),
        _row("Hours/week", application.get("hours_per_week", "—")),
        _row("Duration", application.get("duration_commitment", "—")),
        _row("Preferred channel", application.get("preferred_channel") or "—"),
        _row("In Slack already", application.get("slack_member") or "—"),
        _row("Heard about us via", application.get("referral_source") or "—"),
    ])

    work_sample = (application.get("work_sample_answer") or "").replace("\n", "<br>")
    why = (application.get("why_ohack") or "").replace("\n", "<br>")
    why_block = (f'<p style="font-size: 14px; color: #777; margin-bottom: 4px;">Why Opportunity Hack:</p>'
                 f'<div style="background-color: #f5f5f5; padding: 12px; border-radius: 6px; font-size: 14px;">{why}</div>'
                 if why else "")

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": [JOBS_FYI_EMAIL],
        "subject": f"New volunteer application: {application.get('listing_title', '?')} — {application.get('name', '?')}",
        "html": f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>New application: {application.get('listing_title', '?')}</h2>
            <table style="font-size: 14px; border-collapse: collapse;">{rows}</table>
            <p style="font-size: 14px; color: #777; margin-bottom: 4px;">Work sample answer:</p>
            <div style="background-color: #f5f5f5; padding: 12px; border-radius: 6px; font-size: 14px; border-left: 3px solid #2c5aa0;">{work_sample}</div>
            {why_block}
            <p style="margin-top: 20px;"><a href="{ADMIN_APPLICATIONS_URL}" style="color: #3498db;">Review in the admin panel</a></p>
        </div>
        """,
    }
    _send_and_get_id(params)
    logger.info(f"Sent job application FYI to {JOBS_FYI_EMAIL} for {application_id}")
    return True


def _decision_subject(application: Dict[str, Any], decision: str) -> str:
    title = application.get("listing_title", "volunteer role")
    if decision == "accepted":
        return f"Let's talk! Your Opportunity Hack application — {title}"
    return f"Your Opportunity Hack application — {title}"


def _send_decision_email(application: Dict[str, Any], decision: str,
                         personal_note: str = "") -> Optional[str]:
    if not _resend_ready():
        return None

    name = application.get("name") or "there"
    title = application.get("listing_title", "volunteer role")
    note_block = ""
    if personal_note:
        note_block = f"""
    <div style="background-color: #f5f5f5; padding: 15px; border-left: 3px solid #2c5aa0; margin: 15px 0;">
        <p style="margin: 0; font-size: 15px; color: #34495e;">{personal_note}</p>
    </div>"""

    if decision == "accepted":
        body = f"""
    <h2 style="color: #27ae60; text-align: center;">We'd love to talk!</h2>
    <p style="font-size: 16px;">Hi {name},</p>
    <p style="font-size: 16px;">Great news — we'd like to move forward with your application for
       <strong>{title}</strong>. Your work sample and video stood out.</p>
    {note_block}
    <p style="font-size: 16px;">Expect an email from <strong>questions@ohack.org</strong> shortly to set up a
       short call. In the meantime, if you haven't joined our
       <a href="https://www.ohack.dev/signup" style="color: #3498db;">Slack</a> yet, now is a great time.</p>"""
    else:
        body = f"""
    <h2 style="color: #3498db; text-align: center;">Thank you — truly</h2>
    <p style="font-size: 16px;">Hi {name},</p>
    <p style="font-size: 16px;">Thank you for applying for <strong>{title}</strong>. We know this application
       took real time and thought, and we don't take that lightly.</p>
    <p style="font-size: 16px;">After careful review, we've decided to go in a different direction for this
       role right now. That's a statement about fit for one specific role — not about you or your abilities.</p>
    {note_block}
    <div style="background-color: #e8f5e8; padding: 18px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #27ae60;">
        <h3 style="color: #27ae60; margin: 0 0 8px 0; font-size: 17px;">The door is very much open</h3>
        <p style="margin: 0 0 8px 0; font-size: 15px;">We're a volunteer-run nonprofit and there are many other
           ways to make a real impact (and build your portfolio) with us:</p>
        <p style="margin: 0; font-size: 15px;">
            • <a href="https://www.ohack.dev/hack" style="color: #27ae60;">Mentor, judge, or volunteer at our next hackathon</a><br>
            • <a href="https://www.ohack.dev/projects" style="color: #27ae60;">Contribute to a year-round nonprofit project</a><br>
            • <a href="https://www.ohack.dev/signup" style="color: #27ae60;">Join our Slack community</a>
        </p>
    </div>
    <p style="font-size: 16px;">We'd genuinely love to see you apply again for a future role. Thank you for
       wanting to use your skills for social good.</p>"""

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": [application["email"]],
        "reply_to": JOBS_FYI_EMAIL,
        "subject": _decision_subject(application, decision),
        "html": f"{_EMAIL_SHELL_TOP}{body}{_EMAIL_SHELL_BOTTOM}",
    }
    resend_id = _send_and_get_id(params)
    logger.info(f"Sent {decision} email to {application['email']} (resend_id={resend_id})")
    return resend_id
