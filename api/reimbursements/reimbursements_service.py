"""Reimbursement requests for out-of-pocket food purchases at OHack events.

Public (no login — v1 follows the contact-form pattern): a submitter uploads
an itemized receipt straight to GCS via a signed URL, then POSTs the claim
with the receipt blob path. Admins (``volunteer.admin``) review, approve /
deny, and mark paid.

IRS accountable-plan rules enforced here (informational summary, not tax
advice): a business connection (``business_purpose`` + ``fed_description``),
substantiation (itemized receipt, date, vendor, amount), and timely filing —
``expense_date`` must be within the last 120 days and never in the future.
Return of any excess is an out-of-band treasurer process; v1 collects no
bank details.

Security notes:
- No bank account / routing numbers are stored anywhere in v1. The
  ``reimbursement_method`` field records only ``ach`` or ``check``; ACH
  details are collected later through a secure follow-up, never in this API.
- Server-owned fields (``status``, ``reviewed_by``, ``paid_at``,
  ``status_history``, ``reference_id``, ``*_at`` timestamps) are never
  accepted from a client payload — the admin PATCH reads only ``status``
  and ``reviewNote``.
- No self-approval: a reviewer whose verified PropelAuth email (or propel id)
  matches the submitter's may not change the status (403 ``self_approval``).
  The treasurer is Jot Powers and the president is Greg Vannoni; neither may
  approve their own claim.
- Receipt bytes never pass through Flask: the client PUTs straight to GCS
  with a signed URL minted by ``common.utils.cdn.generate_signed_upload_url``
  (same flow as the jobs resume upload), and submission verifies the blob
  exists via ``get_blob_metadata`` before accepting the claim.
"""

import math
import os
import re
import secrets
import string
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests
from ratelimiter import RateLimiter

from db.db import get_db
from common.log import get_logger

logger = get_logger(__name__)

COLLECTION = "reimbursements"

# IRS substantiation window enforced by the contract (v1): claims older than
# this are rejected. The 60-day substantiation guideline is the treasurer's
# follow-up concern; the API ceiling is 120 days.
MAX_EXPENSE_AGE_DAYS = 120
DATE_SKEW_DAYS = 1  # local-calendar vs UTC tolerance, see validate_expense_date
MAX_AMOUNT_DOLLARS = 10000
MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_RECEIPT_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
ALLOWED_PAYMENT_METHODS = ("personal_card", "personal_cash", "other")
ALLOWED_REIMBURSEMENT_METHODS = ("ach", "check")

STATUSES = ("submitted", "under_review", "needs_info", "approved", "denied", "paid")

# Allowed status transitions. Anything else is a 409 invalid_transition.
# Terminal states: paid (money out the door) and denied (until reopened).
ALLOWED_TRANSITIONS = {
    "submitted": ("under_review", "needs_info", "approved", "denied"),
    "under_review": ("approved", "denied", "needs_info", "submitted"),
    "needs_info": ("under_review", "approved", "denied"),
    "approved": ("paid",),
    "denied": ("under_review",),
    "paid": (),
}

# Fields a client payload must never decide (mirrors
# STAFF_OWNED_VOLUNTEER_FIELDS in services/volunteers_service.py). The admin
# PATCH reads only status + reviewNote; the public POST builds the doc from
# validated fields only, so these are listed here for reviewers, not applied
# as a filter.
SERVER_OWNED_FIELDS = frozenset({
    "status", "reviewed_by", "reviewed_at", "paid_at", "status_history",
    "reference_id", "submitted_at", "created_at", "updated_at",
    "submitter_propel_id", "amount_cents",
})

_REQUIRED_SUBMIT_FIELDS = (
    "submitterName", "submitterEmail", "expenseDate", "vendor", "amount",
    "businessPurpose", "fedDescription", "paymentMethod",
    "reimbursementMethod", "attestation", "receiptBlobPath",
    "receiptFilename", "receiptContentType", "recaptchaToken",
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Receipt paths are minted by create_receipt_upload_url; accept only that
# exact shape so a claim can never point at some other object in the bucket.
_RECEIPT_BLOB_RE = re.compile(
    r"^reimbursements/[0-9a-f]{32}/receipt_[0-9a-f]{32}\.(jpg|png|webp|pdf)$"
)

# Length caps on free text (public, unauthenticated input; a Firestore doc
# is capped at 1 MiB and admins read these in a dialog).
MAX_FIELD_LENGTHS = {
    "submitterName": 200,
    "submitterEmail": 254,
    "phone": 40,
    "vendor": 200,
    "eventId": 200,
    "businessPurpose": 2000,
    "fedDescription": 2000,
    "receiptFilename": 255,
}
MAX_REVIEW_NOTE_LENGTH = 2000

# NOTE on RateLimiter: like api/contact, this is a process-global throttle
# (ratelimiter sleeps when max_calls are exceeded in the period), NOT a
# per-IP limiter — there is no per-key support in the library. It still
# blunts a flood from one worker. Core logic lives in the undecorated
# functions below so unit tests stay fast; the decorated wrappers are the
# only entry points the views call.


def _now_iso() -> str:
    """UTC ISO timestamp (aware, so naive/aware comparisons never raise)."""
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def verify_recaptcha(token: str) -> bool:
    """Verify a reCAPTCHA token with Google's API (same as contact form)."""
    recaptcha_key = os.environ.get("GOOGLE_CAPTCHA_SECRET_KEY")
    if not recaptcha_key:
        logger.warning("No GOOGLE_CAPTCHA_SECRET_KEY set, skipping verification")
        return True
    url = "https://www.google.com/recaptcha/api/siteverify"
    try:
        response = requests.post(
            url, data={"secret": recaptcha_key, "response": token}, timeout=10
        )
        return response.json().get("success", False)
    except Exception as e:
        logger.exception("Error verifying recaptcha: %s", str(e))
        return False


def _audit(action: str, message: str) -> None:
    """Best-effort Slack audit line so the treasurer learns a claim arrived
    or moved. Never raises; silent under ENVIRONMENT=test. Lazy import keeps
    this module importable without Slack config. No email/phone in the text."""
    if os.environ.get("ENVIRONMENT") == "test":
        return
    try:
        from common.utils.slack import send_slack_audit

        send_slack_audit(action=action, message=" ".join(message.split()), payload={})
    except Exception as e:
        logger.warning("Reimbursement audit failed (%s): %s", action, type(e).__name__)


def generate_reference_id(today: Optional[date] = None) -> str:
    """REIMB-YYYY-XXXXXX with 6 random uppercase alphanumerics."""
    year = (today or _today_utc()).year
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return "REIMB-%d-%s" % (year, suffix)


def validate_expense_date(value: Any) -> Optional[str]:
    """Error string, or None when the date is a valid YYYY-MM-DD within the
    allowed window (not in the future, not more than 120 days old)."""
    if not isinstance(value, str):
        return "expenseDate must be a YYYY-MM-DD string"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return "expenseDate must be a valid YYYY-MM-DD date"
    # The submitter picks the date on their own calendar, which can be a day
    # ahead of or behind UTC (9 PM in Arizona is already tomorrow in UTC), so
    # both edges get one day of slack rather than rejecting a date the form
    # just accepted.
    today = _today_utc()
    if (parsed - today).days > DATE_SKEW_DAYS:
        return "expenseDate must not be in the future"
    if (today - parsed).days > MAX_EXPENSE_AGE_DAYS + DATE_SKEW_DAYS:
        return "expenseDate must not be more than %d days old" % MAX_EXPENSE_AGE_DAYS
    return None


def validate_amount(value: Any) -> Optional[int]:
    """Amount in cents, or None when invalid (not a number, <= 0, > $10,000).
    Booleans are rejected explicitly (bool is an int subclass in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # NaN compares False to everything, so it must be rejected explicitly
    # (json.loads accepts a bare NaN) or int(round(nan)) raises -> 500.
    if not math.isfinite(value) or value <= 0 or value > MAX_AMOUNT_DOLLARS:
        return None
    cents = int(round(value * 100))
    return cents if cents >= 1 else None


def _err(code: str, message: str, status: int = 400) -> Tuple[Dict[str, Any], int]:
    return {"success": False, "error": code, "message": message}, status


def _reference_id_exists(db: Any, reference_id: str) -> bool:
    return _find_by_reference(db, reference_id) is not None


def _verify_receipt_blob(
    blob_path: str, filename: str, declared_content_type: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Dict[str, Any], int]]]:
    """Verify the uploaded receipt blob. Returns (receipt_dict, None) on
    success, (None, (error_payload, status)) on failure."""
    if not isinstance(blob_path, str) or not _RECEIPT_BLOB_RE.match(blob_path):
        return None, _err(
            "invalid_receipt", "receiptBlobPath must be a reimbursement upload"
        )
    if declared_content_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        return None, _err(
            "receipt_invalid_type",
            "Receipt must be JPEG, PNG, WebP, or PDF",
        )
    # Lazy import: keeps this module importable without GCS credentials.
    from common.utils.cdn import cdn_server, get_blob_metadata

    try:
        meta = get_blob_metadata(blob_path)
    except Exception as e:
        logger.exception("Failed to verify receipt blob %s: %s", blob_path, e)
        return None, _err(
            "receipt_not_found", "Could not verify the uploaded receipt"
        )
    if not meta.get("exists"):
        return None, _err(
            "receipt_not_found",
            "Receipt upload not found — did the upload finish?",
        )
    blob_type = meta.get("content_type")
    if blob_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        return None, _err(
            "receipt_invalid_type",
            "Uploaded receipt is not an allowed file type",
        )
    size = meta.get("size") or 0
    if size <= 0:
        return None, _err(
            "receipt_not_found",
            "Receipt upload is empty — please upload it again",
        )
    if size > MAX_RECEIPT_BYTES:
        return None, _err(
            "receipt_too_large", "Receipt must be under 10 MB"
        )
    return {
        "blob_path": blob_path,
        "final_url": "%s/%s" % (cdn_server(), blob_path),
        "filename": filename,
        "content_type": blob_type,
        "size_bytes": size,
    }, None


def validate_submission_payload(
    data: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Dict[str, Any], int]]]:
    """Pure validation of a POST /api/reimbursements body (no I/O except the
    receipt-blob check, which lives in create_reimbursement). Returns
    (cleaned, None) or (None, (error_payload, status))."""
    if not isinstance(data, dict):
        return None, _err("missing_fields", "Request body must be a JSON object")
    missing = [
        f for f in _REQUIRED_SUBMIT_FIELDS
        if data.get(f) is None or (isinstance(data.get(f), str) and not data.get(f).strip())
    ]
    if missing:
        return None, _err(
            "missing_fields",
            "Missing required fields: %s" % ", ".join(missing),
        )
    for field, limit in MAX_FIELD_LENGTHS.items():
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None, _err("invalid_field", "%s must be a string" % field)
        if len(value.strip()) > limit:
            return None, _err(
                "field_too_long",
                "%s must be at most %d characters" % (field, limit),
            )
    email = str(data["submitterEmail"]).strip().lower()
    if not _EMAIL_RE.match(email):
        return None, _err("invalid_email", "submitterEmail must be a valid email address")
    amount_cents = validate_amount(data.get("amount"))
    if amount_cents is None:
        return None, _err(
            "invalid_amount",
            "amount must be a number greater than 0 and at most %d" % MAX_AMOUNT_DOLLARS,
        )
    date_error = validate_expense_date(data.get("expenseDate"))
    if date_error:
        return None, _err("invalid_date", date_error)
    if data.get("attestation") is not True:
        return None, _err(
            "invalid_attestation",
            "attestation must be true: you must confirm the expense is real, "
            "business-related, and supported by the attached receipt",
        )
    if data.get("paymentMethod") not in ALLOWED_PAYMENT_METHODS:
        return None, _err(
            "invalid_payment_method",
            "paymentMethod must be one of %s" % ", ".join(ALLOWED_PAYMENT_METHODS),
        )
    if data.get("reimbursementMethod") not in ALLOWED_REIMBURSEMENT_METHODS:
        return None, _err(
            "invalid_reimbursement_method",
            "reimbursementMethod must be one of %s"
            % ", ".join(ALLOWED_REIMBURSEMENT_METHODS),
        )
    return {
        "submitter_name": str(data["submitterName"]).strip(),
        "submitter_email": email,
        "phone": str(data.get("phone") or "").strip(),
        "expense_date": data["expenseDate"],
        "vendor": str(data["vendor"]).strip(),
        "amount_cents": amount_cents,
        "event_id": str(data.get("eventId") or "").strip(),
        "business_purpose": str(data["businessPurpose"]).strip(),
        "fed_description": str(data["fedDescription"]).strip(),
        "payment_method": data["paymentMethod"],
        "reimbursement_method": data["reimbursementMethod"],
        "receipt_filename": str(data["receiptFilename"]).strip(),
        "receipt_content_type": data["receiptContentType"],
        "receipt_blob_path": data["receiptBlobPath"],
    }, None


def create_reimbursement(
    ip_address: Optional[str], data: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Create a reimbursement claim. Returns (payload, status)."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    recaptcha_token = data.get("recaptchaToken")
    if not verify_recaptcha(recaptcha_token) and os.environ.get("FLASK_ENV") != "development":
        logger.warning("reCAPTCHA verification failed for reimbursement submission")
        return _err("invalid_recaptcha", "reCAPTCHA verification failed")
    cleaned, error = validate_submission_payload(data)
    if error:
        return error
    receipt, error = _verify_receipt_blob(
        cleaned["receipt_blob_path"],
        cleaned["receipt_filename"],
        cleaned["receipt_content_type"],
    )
    if error:
        return error
    # Receipt verification passed; the blob path is no longer needed as a
    # top-level field (it lives under receipt.blob_path).
    cleaned.pop("receipt_blob_path")
    cleaned.pop("receipt_filename")
    cleaned.pop("receipt_content_type")

    db = get_db()
    reference_id = generate_reference_id()
    for _ in range(5):
        if not _reference_id_exists(db, reference_id):
            break
        reference_id = generate_reference_id()
    now = _now_iso()
    doc_id = str(uuid.uuid4())
    doc = dict(cleaned)
    doc.update({
        "id": doc_id,
        "submitter_propel_id": None,  # public submitters have no login (v1)
        "currency": "USD",
        "attestation": True,
        "receipt": receipt,
        "reference_id": reference_id,
        "status": "submitted",
        "status_history": [{"status": "submitted", "at": now, "by": "applicant"}],
        "submitted_at": now,
        "created_at": now,
        "updated_at": now,
        "reviewed_by": None,
        "reviewed_at": None,
        "paid_at": None,
        "review_note": "",
        "ip_address": ip_address or "",
    })
    db.collection(COLLECTION).document(doc_id).set(doc)
    logger.info("Reimbursement %s created (%s)", reference_id, doc_id)
    _audit(
        "reimbursement_submitted",
        "%s: %s requested $%.2f (%s) — review at /admin/reimbursements"
        % (reference_id, doc["submitter_name"], doc["amount_cents"] / 100.0, doc["vendor"]),
    )
    return {
        "success": True,
        "id": doc_id,
        "referenceId": reference_id,
        "status": "submitted",
    }, 201


@RateLimiter(max_calls=5, period=60)
def submit_reimbursement(
    ip_address: Optional[str], data: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Rate-limited entry point for POST /api/reimbursements (see note above)."""
    return create_reimbursement(ip_address, data)


def create_receipt_upload_url(
    ip_address: Optional[str], data: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Mint a signed GCS PUT URL for a receipt. Returns (payload, status)."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    recaptcha_token = data.get("recaptchaToken")
    if not verify_recaptcha(recaptcha_token) and os.environ.get("FLASK_ENV") != "development":
        return _err("invalid_recaptcha", "reCAPTCHA verification failed")
    content_type = data.get("contentType")
    if content_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        return _err(
            "receipt_invalid_type",
            "Receipt must be JPEG, PNG, WebP, or PDF",
        )
    try:
        content_length = int(data.get("contentLength"))
    except (TypeError, ValueError, OverflowError):
        return _err("receipt_missing", "contentLength is required")
    if content_length <= 0 or content_length > MAX_RECEIPT_BYTES:
        return _err("receipt_too_large", "Receipt must be under 10 MB")
    ext = ALLOWED_RECEIPT_CONTENT_TYPES[content_type]
    # Server-generated filename: sanitized by construction, no user input in
    # the blob path. One uuid4 directory per receipt.
    filename = "receipt_%s.%s" % (uuid.uuid4().hex, ext)
    directory = "reimbursements/%s" % uuid.uuid4().hex
    from common.utils.cdn import generate_signed_upload_url

    try:
        payload = generate_signed_upload_url(
            directory=directory,
            filename=filename,
            content_type=content_type,
            max_bytes=MAX_RECEIPT_BYTES,
        )
    except Exception as e:
        logger.exception("Failed to generate signed receipt upload URL: %s", e)
        return _err("upload_url_failed", "Could not create an upload URL", 500)
    return payload, 200


@RateLimiter(max_calls=10, period=60)
def mint_receipt_upload_url(
    ip_address: Optional[str], data: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Rate-limited entry point for POST /api/reimbursements/receipt-upload-url."""
    return create_receipt_upload_url(ip_address, data)


def _public_status_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The only fields the public status lookup may reveal — never receipt
    URLs or reviewer notes."""
    return {
        "referenceId": doc.get("reference_id"),
        "status": doc.get("status"),
        "amountCents": doc.get("amount_cents"),
        "expenseDate": doc.get("expense_date"),
        "updatedAt": doc.get("updated_at"),
    }


def _admin_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(doc)
    view["id"] = doc.get("id")
    return view


def _find_by_reference(db: Any, reference_id: str) -> Optional[Dict[str, Any]]:
    # Single-field equality is auto-indexed (no composite index). This runs
    # on the PUBLIC status route, so it must never scan the whole collection.
    query = db.collection(COLLECTION).where("reference_id", "==", reference_id).limit(1)
    for doc in query.stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        return data
    return None


def get_reimbursement_status(
    email: Optional[str], reference_id: Optional[str]
) -> Tuple[Dict[str, Any], int]:
    """Public status lookup. 404 unless the reference id exists AND the
    email matches the submitter (case-insensitive)."""
    if not email or not reference_id:
        return _err(
            "missing_fields", "email and reference_id query params are required"
        )
    db = get_db()
    doc = _find_by_reference(db, reference_id.strip().upper())
    if doc is None:
        return {"success": False, "error": "not_found",
                "message": "No reimbursement matches that email and reference id"}, 404
    if str(doc.get("submitter_email") or "").lower() != email.strip().lower():
        return {"success": False, "error": "not_found",
                "message": "No reimbursement matches that email and reference id"}, 404
    return _public_status_view(doc), 200


def admin_list_reimbursements(
    status: Optional[str] = None,
    event_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Dict[str, Any], int]:
    """Admin list, newest first. Filters + sort in Python (no composite
    index). ``count`` is the total matching, before limit/offset."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    db = get_db()
    results = []
    for doc in db.collection(COLLECTION).stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        if status and data.get("status") != status:
            continue
        if event_id and data.get("event_id") != event_id:
            continue
        results.append(data)
    results.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)
    total = len(results)
    page = [_admin_view(item) for item in results[offset:offset + limit]]
    return {"data": page, "count": total}, 200


def admin_get_reimbursement(doc_id: str) -> Tuple[Dict[str, Any], int]:
    """Full admin detail, including receipt URL, status history, reviewer info."""
    db = get_db()
    snap = db.collection(COLLECTION).document(doc_id).get()
    if snap is None or not snap.exists:
        return {"success": False, "error": "not_found",
                "message": "Reimbursement not found"}, 404
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return _admin_view(data), 200


def admin_review_reimbursement(
    doc_id: str,
    data: Optional[Dict[str, Any]],
    reviewer: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    """Admin PATCH. Only ``status`` and ``reviewNote`` are read from the
    payload — status/reviewed_by/paid_at/status_history from the client are
    ignored (server-owned). Enforces the transition map and no self-approval."""
    if not isinstance(data, dict):
        return _err("missing_fields", "Request body must be a JSON object")
    reviewer = reviewer or {}
    new_status = data.get("status")
    review_note = data.get("reviewNote")
    if new_status is None and review_note is None:
        return _err(
            "missing_fields", "Provide status and/or reviewNote"
        )
    if review_note is not None and not isinstance(review_note, str):
        return _err("invalid_review_note", "reviewNote must be a string")
    if review_note is not None:
        review_note = review_note.strip()
        if len(review_note) > MAX_REVIEW_NOTE_LENGTH:
            return _err(
                "field_too_long",
                "reviewNote must be at most %d characters" % MAX_REVIEW_NOTE_LENGTH,
            )
    if new_status is not None and (
        not isinstance(new_status, str) or new_status not in STATUSES
    ):
        return _err(
            "invalid_status",
            "status must be one of %s" % ", ".join(STATUSES),
        )
    db = get_db()
    doc_ref = db.collection(COLLECTION).document(doc_id)
    snap = doc_ref.get()
    if snap is None or not snap.exists:
        return {"success": False, "error": "not_found",
                "message": "Reimbursement not found"}, 404
    existing = snap.to_dict() or {}

    # No self-approval: the reviewer's verified PropelAuth email (and propel
    # id, when the submitter logged in) must not match the submitter.
    reviewer_email = (reviewer.get("email") or "").strip().lower()
    reviewer_propel_id = reviewer.get("propel_id")
    submitter_email = str(existing.get("submitter_email") or "").lower()
    submitter_propel_id = existing.get("submitter_propel_id")
    # Fail closed: without a verified reviewer identity the no-self-approval
    # rule can't be checked and the history would say an anonymous "admin".
    if not reviewer_email and not reviewer_propel_id:
        return {"success": False, "error": "reviewer_unknown",
                "message": "Could not verify who is reviewing this claim"}, 403
    if reviewer_email and reviewer_email == submitter_email:
        return {"success": False, "error": "self_approval",
                "message": "You cannot review your own reimbursement claim"}, 403
    if (reviewer_propel_id and submitter_propel_id
            and reviewer_propel_id == submitter_propel_id):
        return {"success": False, "error": "self_approval",
                "message": "You cannot review your own reimbursement claim"}, 403

    now = _now_iso()
    update: Dict[str, Any] = {"updated_at": now}
    if review_note is not None:
        update["review_note"] = review_note
    if new_status is not None:
        if new_status == existing.get("status"):
            # Same-status is not a transition in the map — 409, not a silent
            # no-op (a note-only update omits status instead).
            return {"success": False, "error": "invalid_transition",
                    "message": "Claim is already %s" % new_status}, 409
        allowed = ALLOWED_TRANSITIONS.get(existing.get("status"), ())
        if new_status not in allowed:
            return {"success": False, "error": "invalid_transition",
                    "message": "Cannot move a %s claim to %s"
                    % (existing.get("status"), new_status)}, 409
        update["status"] = new_status
        history = list(existing.get("status_history") or [])
        history.append({
            "status": new_status,
            "at": now,
            "by": reviewer_email or "admin",
            "note": review_note or "",
        })
        update["status_history"] = history
        update["reviewed_by"] = {
            "propel_id": reviewer_propel_id,
            "email": reviewer_email or None,
            "name": reviewer.get("name"),
        }
        update["reviewed_at"] = now
        if new_status == "paid":
            update["paid_at"] = now
    doc_ref.update(update)
    if new_status is not None:
        _audit(
            "reimbursement_reviewed",
            "%s: %s -> %s by %s"
            % (existing.get("reference_id"), existing.get("status"), new_status,
               reviewer_email or reviewer_propel_id),
        )
    logger.info(
        "Reimbursement %s reviewed: %s -> %s by %s",
        doc_id, existing.get("status"), new_status or existing.get("status"),
        reviewer_email or "admin",
    )
    return {
        "success": True,
        "id": doc_id,
        "status": new_status if new_status is not None else existing.get("status"),
        "reference_id": existing.get("reference_id"),
    }, 200
