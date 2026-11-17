"""Unit tests for the reimbursements service.

MockFirestore + patched reCAPTCHA / GCS metadata, following
test/services/test_volunteer_slack_refresh.py. ENVIRONMENT=test so no real
Slack/Resend sends happen (this service sends none in v1 anyway).
"""
import os
import re
from datetime import date, timedelta

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from unittest.mock import patch
from mockfirestore import MockFirestore

from api.reimbursements import reimbursements_service as svc


def _today_str():
    # UTC, like the service: a local-clock date made these fail every
    # evening in US timezones.
    return svc._today_utc().isoformat()


def _valid_payload(**overrides):
    payload = {
        "submitterName": "Ada Lovelace",
        "submitterEmail": "Ada@Example.com",
        "phone": "480-555-0100",
        "expenseDate": _today_str(),
        "vendor": "Costco",
        "amount": 123.45,
        "eventId": "fall-2026",
        "businessPurpose": "Lunch for hackathon volunteers",
        "fedDescription": "30 volunteers and mentors",
        "paymentMethod": "personal_card",
        "reimbursementMethod": "ach",
        "attestation": True,
        "receiptBlobPath": "reimbursements/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/receipt_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        "receiptFilename": "receipt.jpg",
        "receiptContentType": "image/jpeg",
        "recaptchaToken": "test-token",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def db():
    return MockFirestore()


@pytest.fixture
def env(db):
    """Patch the service's I/O boundaries: Firestore, reCAPTCHA, GCS."""
    with patch.object(svc, "get_db", return_value=db), \
        patch.object(svc, "verify_recaptcha", return_value=True), \
        patch("common.utils.cdn.get_blob_metadata", return_value={
            "exists": True, "size": 12345, "content_type": "image/jpeg",
        }), \
        patch("common.utils.cdn.cdn_server", return_value="https://cdn.ohack.dev"):
        yield db


def _get_doc(db, doc_id):
    return db.collection(svc.COLLECTION).document(doc_id).get().to_dict()


def _submit(env, **overrides):
    payload, status = svc.create_reimbursement("127.0.0.1", _valid_payload(**overrides))
    return payload, status


# ---------------------------------------------------------------------------
# Reference ids
# ---------------------------------------------------------------------------

def test_reference_id_format():
    ref = svc.generate_reference_id()
    assert re.match(r"^REIMB-\d{4}-[A-Z0-9]{6}$", ref), ref


def test_reference_id_uses_submission_year():
    ref = svc.generate_reference_id(today=date(2026, 11, 15))
    assert ref.startswith("REIMB-2026-")


# ---------------------------------------------------------------------------
# Validation: amount / date / attestation / missing fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [0, -5, 10000.01, "123.45", True])
def test_invalid_amounts_rejected(env, bad):
    payload, status = _submit(env, amount=bad)
    assert status == 400
    assert payload["error"] == "invalid_amount"


def test_amount_none_is_missing_field(env):
    payload, status = _submit(env, amount=None)
    assert status == 400
    assert payload["error"] == "missing_fields"


def test_amount_boundary_10000_ok(env):
    payload, status = _submit(env, amount=10000)
    assert status == 201
    assert _get_doc(env, payload["id"])["amount_cents"] == 1000000


def test_amount_cents_rounding(env):
    payload, status = _submit(env, amount=123.45)
    assert status == 201
    assert _get_doc(env, payload["id"])["amount_cents"] == 12345


def test_future_expense_date_rejected(env):
    future = (svc._today_utc() + timedelta(days=2)).isoformat()
    payload, status = _submit(env, expenseDate=future)
    assert status == 400
    assert payload["error"] == "invalid_date"


def test_expense_date_over_120_days_rejected(env):
    old = (svc._today_utc() - timedelta(days=122)).isoformat()
    payload, status = _submit(env, expenseDate=old)
    assert status == 400
    assert payload["error"] == "invalid_date"


def test_expense_date_120_days_ok(env):
    old = (svc._today_utc() - timedelta(days=120)).isoformat()
    payload, status = _submit(env, expenseDate=old)
    assert status == 201


def test_malformed_expense_date_rejected(env):
    payload, status = _submit(env, expenseDate="11/15/2026")
    assert status == 400
    assert payload["error"] == "invalid_date"


def test_missing_fields_listed(env):
    data = _valid_payload()
    del data["vendor"]
    del data["businessPurpose"]
    payload, status = svc.create_reimbursement("127.0.0.1", data)
    assert status == 400
    assert payload["error"] == "missing_fields"
    assert "vendor" in payload["message"] and "businessPurpose" in payload["message"]


def test_invalid_email_rejected(env):
    payload, status = _submit(env, submitterEmail="not-an-email")
    assert status == 400
    assert payload["error"] == "invalid_email"


def test_attestation_false_rejected(env):
    payload, status = _submit(env, attestation=False)
    assert status == 400
    assert payload["error"] == "invalid_attestation"


def test_bad_payment_and_reimbursement_methods_rejected(env):
    payload, status = _submit(env, paymentMethod="company_card")
    assert status == 400 and payload["error"] == "invalid_payment_method"
    payload, status = _submit(env, reimbursementMethod="venmo")
    assert status == 400 and payload["error"] == "invalid_reimbursement_method"


def test_invalid_recaptcha_rejected(env, monkeypatch):
    # The repo .env sets FLASK_ENV=development, where reCAPTCHA is skipped
    # (same as the contact form) — force a non-dev env for this test.
    monkeypatch.setenv("FLASK_ENV", "test")
    with patch.object(svc, "verify_recaptcha", return_value=False):
        payload, status = svc.create_reimbursement("127.0.0.1", _valid_payload())
    assert status == 400
    assert payload["error"] == "invalid_recaptcha"


# ---------------------------------------------------------------------------
# Receipt verification
# ---------------------------------------------------------------------------

def test_receipt_blob_path_outside_reimbursements_rejected(env):
    payload, status = _submit(env, receiptBlobPath="job_applications/x/resume.pdf")
    assert status == 400
    assert payload["error"] == "invalid_receipt"


def test_receipt_blob_not_found(env):
    with patch("common.utils.cdn.get_blob_metadata",
               return_value={"exists": False, "size": None, "content_type": None}):
        payload, status = _submit(env)
    assert status == 400
    assert payload["error"] == "receipt_not_found"


def test_receipt_wrong_content_type_rejected(env):
    with patch("common.utils.cdn.get_blob_metadata",
               return_value={"exists": True, "size": 100, "content_type": "video/mp4"}):
        payload, status = _submit(env)
    assert status == 400
    assert payload["error"] == "receipt_invalid_type"


def test_receipt_too_large_rejected(env):
    with patch("common.utils.cdn.get_blob_metadata",
               return_value={"exists": True, "size": 11 * 1024 * 1024,
                             "content_type": "image/jpeg"}):
        payload, status = _submit(env)
    assert status == 400
    assert payload["error"] == "receipt_too_large"


# ---------------------------------------------------------------------------
# Happy path: stored doc shape
# ---------------------------------------------------------------------------

def test_success_stores_snake_case_doc(env):
    payload, status = _submit(env)
    assert status == 201
    assert payload == {"success": True, "id": payload["id"],
                       "referenceId": payload["referenceId"], "status": "submitted"}
    doc = _get_doc(env, payload["id"])
    assert doc["submitter_email"] == "ada@example.com"  # lowercased
    assert doc["submitter_name"] == "Ada Lovelace"
    assert doc["amount_cents"] == 12345
    assert doc["currency"] == "USD"
    assert doc["attestation"] is True
    assert doc["submitter_propel_id"] is None
    assert doc["receipt"]["blob_path"] == "reimbursements/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/receipt_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    assert doc["receipt"]["final_url"] == (
        "https://cdn.ohack.dev/reimbursements/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/receipt_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg")
    assert doc["receipt"]["size_bytes"] == 12345
    assert doc["status"] == "submitted"
    assert doc["status_history"] == [
        {"status": "submitted", "at": doc["submitted_at"], "by": "applicant"}]
    assert "bank" not in str(doc).lower() and "routing" not in str(doc).lower()
    assert doc["reviewed_by"] is None and doc["paid_at"] is None


def test_rate_limited_wrapper_delegates(env):
    # One call through the decorated entry point (kept to one: the limiter
    # sleeps after 5 calls in 60s, so tests use create_reimbursement directly).
    payload, status = svc.submit_reimbursement("127.0.0.1", _valid_payload())
    assert status == 201 and payload["status"] == "submitted"


# ---------------------------------------------------------------------------
# Receipt upload URL minting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content_type,ext", [
    ("image/jpeg", "jpg"), ("image/png", "png"),
    ("image/webp", "webp"), ("application/pdf", "pdf"),
])
def test_upload_url_mint_ok(env, content_type, ext):
    def _fake_mint(*args, **kwargs):
        return {"upload_url": "https://signed.example/put",
                "blob_path": "reimbursements/x/receipt_y.%s" % ext,
                "final_url": "https://cdn.ohack.dev/reimbursements/x/receipt_y.%s" % ext,
                "required_headers": {"Content-Type": content_type}}

    with patch("common.utils.cdn.generate_signed_upload_url",
               side_effect=_fake_mint) as mint:
        payload, status = svc.create_receipt_upload_url("127.0.0.1", {
            "contentType": content_type, "contentLength": 5000,
            "recaptchaToken": "t",
        })
    assert status == 200
    assert payload["upload_url"] == "https://signed.example/put"
    assert payload["blob_path"].startswith("reimbursements/")
    assert payload["blob_path"].endswith("." + ext)
    assert mint.call_args.kwargs["max_bytes"] == 10 * 1024 * 1024


def test_upload_url_rejects_bad_type_and_size(env):
    payload, status = svc.create_receipt_upload_url("127.0.0.1", {
        "contentType": "video/mp4", "contentLength": 100, "recaptchaToken": "t",
    })
    assert status == 400 and payload["error"] == "receipt_invalid_type"
    payload, status = svc.create_receipt_upload_url("127.0.0.1", {
        "contentType": "application/pdf", "contentLength": 11 * 1024 * 1024,
        "recaptchaToken": "t",
    })
    assert status == 400 and payload["error"] == "receipt_too_large"


# ---------------------------------------------------------------------------
# Public status lookup
# ---------------------------------------------------------------------------

def test_status_lookup_returns_public_projection_only(env):
    payload, _ = _submit(env)
    got, status = svc.get_reimbursement_status("ADA@example.com", payload["referenceId"])
    assert status == 200
    assert got == {
        "referenceId": payload["referenceId"],
        "status": "submitted",
        "amountCents": 12345,
        "expenseDate": _today_str(),
        "updatedAt": got["updatedAt"],
    }
    assert "receipt" not in got and "review" not in str(got).lower()


def test_status_lookup_wrong_email_is_404(env):
    payload, _ = _submit(env)
    got, status = svc.get_reimbursement_status("someone-else@example.com",
                                               payload["referenceId"])
    assert status == 404
    got, status = svc.get_reimbursement_status("ada@example.com", "REIMB-2026-XXXXXX")
    assert status == 404


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def _seed(env, email="ada@example.com", **overrides):
    data = _valid_payload(submitterEmail=email, **overrides)
    payload, status = svc.create_reimbursement("127.0.0.1", data)
    assert status == 201
    return payload["id"]


def _reviewer(email="treasurer@example.com"):
    return {"propel_id": "propel-treasurer", "email": email, "name": "Treasurer"}


def _move_to(env, doc_id, target):
    """Walk the transition map to `target` from submitted (paid needs
    submitted -> approved -> paid; denied is one step)."""
    path = {"submitted": [], "under_review": ["under_review"],
            "needs_info": ["needs_info"], "approved": ["approved"],
            "denied": ["denied"], "paid": ["approved", "paid"]}[target]
    for step in path:
        payload, status = svc.admin_review_reimbursement(
            doc_id, {"status": step}, _reviewer())
        assert status == 200, payload


@pytest.mark.parametrize("from_to", [
    ("submitted", "under_review"), ("submitted", "needs_info"),
    ("submitted", "approved"), ("submitted", "denied"),
    ("under_review", "approved"), ("under_review", "denied"),
    ("under_review", "needs_info"), ("under_review", "submitted"),
    ("needs_info", "under_review"), ("needs_info", "approved"),
    ("needs_info", "denied"), ("approved", "paid"),
    ("denied", "under_review"),
])
def test_valid_transitions(env, from_to):
    start, end = from_to
    doc_id = _seed(env)
    _move_to(env, doc_id, start)
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": end}, _reviewer())
    assert status == 200, payload
    assert payload["status"] == end
    doc = _get_doc(env, doc_id)
    assert doc["status"] == end
    assert doc["status_history"][-1]["status"] == end
    assert doc["reviewed_by"]["email"] == "treasurer@example.com"
    assert doc["reviewed_at"]
    if end == "paid":
        assert doc["paid_at"]


@pytest.mark.parametrize("from_to", [
    ("submitted", "paid"), ("submitted", "submitted"),
    ("under_review", "paid"), ("needs_info", "paid"),
    ("needs_info", "submitted"), ("approved", "denied"),
    ("approved", "under_review"), ("approved", "submitted"),
    ("denied", "approved"), ("denied", "paid"), ("denied", "submitted"),
    ("paid", "under_review"), ("paid", "submitted"),
])
def test_invalid_transitions_are_409(env, from_to):
    start, end = from_to
    doc_id = _seed(env)
    _move_to(env, doc_id, start)
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": end}, _reviewer())
    assert status == 409
    assert payload["error"] == "invalid_transition"
    assert _get_doc(env, doc_id)["status"] == start


def test_review_unknown_status_and_unknown_doc(env):
    doc_id = _seed(env)
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": "refunded"}, _reviewer())
    assert status == 400 and payload["error"] == "invalid_status"
    payload, status = svc.admin_review_reimbursement(
        "nope", {"status": "approved"}, _reviewer())
    assert status == 404


def test_client_supplied_server_fields_are_ignored(env):
    doc_id = _seed(env)
    payload, status = svc.admin_review_reimbursement(doc_id, {
        "status": "approved",
        "reviewedBy": {"propelId": "attacker", "email": "attacker@example.com"},
        "paidAt": "2020-01-01T00:00:00+00:00",
        "statusHistory": [{"status": "paid", "at": "2020-01-01", "by": "attacker"}],
        "reviewNote": "Looks legit",
    }, _reviewer())
    assert status == 200
    doc = _get_doc(env, doc_id)
    assert doc["reviewed_by"]["email"] == "treasurer@example.com"
    assert doc["paid_at"] is None  # approved, not paid; client value ignored
    assert [h["status"] for h in doc["status_history"]] == ["submitted", "approved"]
    assert doc["review_note"] == "Looks legit"


# ---------------------------------------------------------------------------
# No self-approval
# ---------------------------------------------------------------------------

def test_self_approval_by_email_rejected_case_insensitive(env):
    doc_id = _seed(env, email="Jot@example.com")
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": "approved"}, _reviewer(email="jot@EXAMPLE.com"))
    assert status == 403
    assert payload["error"] == "self_approval"
    assert _get_doc(env, doc_id)["status"] == "submitted"


def test_self_approval_by_propel_id_rejected(env):
    doc_id = _seed(env)
    # Simulate a logged-in submitter: store their propel id on the doc.
    env.collection(svc.COLLECTION).document(doc_id).update(
        {"submitter_propel_id": "propel-submitter"})
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": "approved"},
        {"propel_id": "propel-submitter", "email": "other@example.com"})
    assert status == 403
    assert payload["error"] == "self_approval"


def test_different_reviewer_can_approve(env):
    doc_id = _seed(env, email="ada@example.com")
    payload, status = svc.admin_review_reimbursement(
        doc_id, {"status": "approved"}, _reviewer(email="greg@example.com"))
    assert status == 200


# ---------------------------------------------------------------------------
# Admin list / get
# ---------------------------------------------------------------------------

def test_admin_list_filters_and_paginates(env):
    _seed(env, email="a@example.com", eventId="fall-2026")
    second = _seed(env, email="b@example.com", eventId="fall-2026")
    _seed(env, email="c@example.com", eventId="spring-2027")
    svc.admin_review_reimbursement(second, {"status": "approved"}, _reviewer())
    payload, status = svc.admin_list_reimbursements(event_id="fall-2026")
    assert status == 200 and payload["count"] == 2
    assert len(payload["data"]) == 2
    payload, _ = svc.admin_list_reimbursements(status="approved")
    assert payload["count"] == 1
    assert payload["data"][0]["submitter_email"] == "b@example.com"
    payload, _ = svc.admin_list_reimbursements(limit=1, offset=1)
    assert len(payload["data"]) == 1 and payload["count"] == 3
    payload, _ = svc.admin_list_reimbursements(limit=500)
    assert len(payload["data"]) == 3  # clamped to 200, all 3 returned


def test_admin_get_full_doc_and_404(env):
    doc_id = _seed(env)
    payload, status = svc.admin_get_reimbursement(doc_id)
    assert status == 200
    assert payload["receipt"]["final_url"].startswith("https://cdn.ohack.dev/")
    assert payload["status_history"]
    payload, status = svc.admin_get_reimbursement("missing")
    assert status == 404


# ---------------------------------------------------------------------------
# Review hardening (peer review of #373)
# ---------------------------------------------------------------------------

def test_nan_and_sub_cent_amounts_rejected(env):
    for bad in (float("nan"), float("inf"), 0.001):
        payload, status = _submit(env, amount=bad)
        assert status == 400 and payload["error"] == "invalid_amount", bad


@pytest.mark.parametrize("path", [
    "reimbursements/../users/x/receipt.jpg",
    "reimbursements/abc/receipt_abc.jpg",
    "reimbursements/%s/receipt_%s.exe" % ("a" * 32, "a" * 32),
])
def test_receipt_blob_path_must_match_minted_shape(env, path):
    payload, status = _submit(env, receiptBlobPath=path)
    assert status == 400 and payload["error"] == "invalid_receipt"


def test_empty_receipt_blob_rejected(env):
    with patch("common.utils.cdn.get_blob_metadata", return_value={
            "exists": True, "size": 0, "content_type": "image/jpeg"}):
        payload, status = _submit(env)
    assert status == 400 and payload["error"] == "receipt_not_found"


def test_overlong_and_non_string_fields_rejected(env):
    payload, status = _submit(env, businessPurpose="x" * 2001)
    assert status == 400 and payload["error"] == "field_too_long"
    payload, status = _submit(env, vendor=["Costco"])
    assert status == 400 and payload["error"] == "invalid_field"
    payload, status = _submit(env, vendor="   ")
    assert status == 400 and payload["error"] == "missing_fields"


def test_non_object_bodies_are_400(env):
    assert svc.create_reimbursement("ip", ["x"])[1] == 400
    assert svc.create_receipt_upload_url("ip", "x")[1] == 400
    assert svc.admin_review_reimbursement("id", ["x"], _reviewer())[1] == 400


def test_status_lookup_is_case_insensitive_on_reference(env):
    created, _ = _submit(env)
    payload, status = svc.get_reimbursement_status(
        "ada@example.com", "  " + created["referenceId"].lower() + " ")
    assert status == 200 and payload["referenceId"] == created["referenceId"]


def test_review_without_reviewer_identity_fails_closed(env):
    created, _ = _submit(env)
    for reviewer in (None, {}, {"email": "", "propel_id": None}):
        payload, status = svc.admin_review_reimbursement(
            created["id"], {"status": "approved"}, reviewer)
        assert status == 403 and payload["error"] == "reviewer_unknown"
    assert _get_doc(env, created["id"])["status"] == "submitted"


def test_review_note_trimmed_and_capped(env):
    created, _ = _submit(env)
    payload, status = svc.admin_review_reimbursement(
        created["id"], {"reviewNote": "x" * 2001}, _reviewer())
    assert status == 400 and payload["error"] == "field_too_long"
    svc.admin_review_reimbursement(
        created["id"], {"status": "needs_info", "reviewNote": "  need totals  "},
        _reviewer())
    assert _get_doc(env, created["id"])["review_note"] == "need totals"


def test_expense_date_tolerates_one_day_of_timezone_skew(env):
    # A submitter whose local calendar is a day ahead of / behind UTC.
    ahead = (svc._today_utc() + timedelta(days=1)).isoformat()
    assert _submit(env, expenseDate=ahead)[1] == 201
    oldest_local = (svc._today_utc() - timedelta(days=121)).isoformat()
    assert _submit(env, expenseDate=oldest_local)[1] == 201
