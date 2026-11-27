"""
Participation letters: eligibility rows, issue, stream, verify, admin review.

Firestore collection `letters` (doc id = 12 random base32 chars):
  user_id, email, event_id, role, name_on_letter, show_title_line,
  facts (frozen snapshot), template_version, signer {name, title},
  status (issued | revoked), pdf_path, pdf_sha256, issued_at,
  revoked_by, revoked_at, revoked_reason, exception (bool), exception_note,
  issued_by (admin propel id, exceptions only)

`letter_keys/<user_id>__<event_id>__<role>` makes issuing idempotent: it is
CREATED (fails if it exists) before anything is rendered, so a double click
gets the same letter back instead of two letters.

The final PDF is rendered ONCE at issue and stored in the CDN bucket under an
unguessable path (`letters/<random>/<id>.pdf`); it is only ever streamed back
through GET /api/letters/<id>/pdf (owner or admin), never linked. `pdf_sha256`
is checked on every read, so the bytes are the same every time.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz

from common.log import get_logger, info, warning
from api.letters import letter_facts as lf
from api.letters import letter_pdf

logger = get_logger("letters_service")

LETTERS = "letters"
LETTER_KEYS = "letter_keys"
RATE_LIMITS = "letter_rate_limits"
DAILY_ISSUE_LIMIT = 10

STATUS_ISSUED = "issued"
STATUS_REVOKED = "revoked"

_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
ID_LENGTH = 12

MAX_NOTE_LENGTH = 1000

# A letter key older than this with no letter doc behind it is a failed issue
# and may be taken over.
KEY_STALE_AFTER = timedelta(minutes=2)


class LetterError(Exception):
    """A refusal the views turn into `{error, message, ...}` with `status`."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


def _db():
    from db.db import get_db
    return get_db()


def _now() -> datetime:
    return datetime.now(pytz.utc)


def new_letter_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(ID_LENGTH))


def is_letter_id(value: str) -> bool:
    return (isinstance(value, str) and len(value) == ID_LENGTH
            and all(c in _ID_ALPHABET for c in value))


def _audit(action: str, message: str, payload: Dict[str, Any]):
    try:
        from common.utils.slack import send_slack_audit
        send_slack_audit(action=action, message=message, payload=payload)
    except Exception as e:
        warning(logger, "letters: slack audit failed", action=action, exc_info=e)


# --------------------------------------------------------------------------
# Who is asking
# --------------------------------------------------------------------------

def _raw_user_doc(doc_id: Optional[str]) -> Dict[str, Any]:
    if not doc_id:
        return {}
    try:
        snap = _db().collection("users").document(doc_id).get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception as e:
        warning(logger, "letters: user doc read failed", exc_info=e)
        return {}


def subject_for_caller(propel_id: str, token_email: Optional[str]) -> Dict[str, Any]:
    """The person behind a request: PropelAuth id, verified email and the
    profile fields a letter may print. `doc_ids` = every user doc of theirs
    (team rosters may hold either of a Google/Slack pair)."""
    from services.users_service import resolve_caller
    caller = resolve_caller(propel_id)
    user = getattr(caller, "user", None)
    raw = _raw_user_doc(getattr(user, "id", None))
    return {
        "user_id": propel_id,
        "email": (token_email or getattr(user, "email_address", "") or "").strip().lower() or None,
        "user_doc_id": getattr(user, "id", None),
        "doc_ids": set(getattr(caller, "doc_ids", None) or ()),
        "name": getattr(user, "name", "") or raw.get("name") or "",
        "legal_name": raw.get("legal_name") or "",
        "title": getattr(user, "role", "") or raw.get("role") or "",
        "company": getattr(user, "company", "") or raw.get("company") or "",
    }


def subject_for_volunteer(vol: Dict[str, Any]) -> Dict[str, Any]:
    """The person behind an application, for admin views — the stored
    PropelAuth id + email on the volunteer doc, plus their user doc (by
    propel_id, else email) for legal_name / title / company."""
    propel_id = vol.get("user_id") or None
    email = (vol.get("email") or "").strip().lower() or None
    raw, doc_id = {}, None
    db = _db()
    for field, value in (("propel_id", propel_id), ("email_address", email)):
        if not value or raw:
            continue
        try:
            for snap in db.collection("users").where(field, "==", value).limit(1).stream():
                raw, doc_id = snap.to_dict() or {}, snap.id
        except Exception as e:
            warning(logger, "letters: user lookup failed", field=field, exc_info=e)
    return {
        "user_id": propel_id,
        "email": email,
        "user_doc_id": doc_id,
        "doc_ids": {doc_id} if doc_id else set(),
        "name": raw.get("name") or lf.volunteer_display_name(vol),
        "legal_name": raw.get("legal_name") or "",
        "title": raw.get("role") or "",
        "company": raw.get("company") or "",
    }


def _caller_judge_ids(propel_id: str) -> List[str]:
    from api.judging.judge_identity import caller_judge_ids
    return caller_judge_ids(propel_id)


# --------------------------------------------------------------------------
# Letter docs
# --------------------------------------------------------------------------

def _key_id(user_id: str, event_id: str, role: str) -> str:
    return hashlib.sha256(f"{user_id}\x1f{event_id}\x1f{role}".encode()).hexdigest()[:40]


def _letter_doc(letter_id: str) -> Optional[Dict[str, Any]]:
    if not is_letter_id(letter_id):
        return None
    snap = _db().collection(LETTERS).document(letter_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    d["id"] = snap.id
    return d


def _letters_where(field: str, value: str) -> List[Dict[str, Any]]:
    out = []
    for snap in _db().collection(LETTERS).where(field, "==", value).stream():
        d = snap.to_dict() or {}
        d["id"] = snap.id
        out.append(d)
    return out


def find_letter(user_id: str, event_id: str, role: str) -> Optional[Dict[str, Any]]:
    for d in _letters_where("user_id", user_id):
        if d.get("event_id") == event_id and d.get("role") == role:
            return d
    return None


def _iso(value) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = pytz.utc.localize(value)
        return value.astimezone(pytz.utc).isoformat().replace("+00:00", "Z")
    return value or None


def _check_rate_limit(user_id: str):
    """10 issue requests per user per (UTC) day, counted in Firestore so the
    limit holds across workers and machines."""
    ref = _db().collection(RATE_LIMITS).document(user_id)
    today = _now().date().isoformat()
    snap = ref.get()
    data = (snap.to_dict() or {}) if snap.exists else {}
    count = data.get("count", 0) if data.get("day") == today else 0
    if count >= DAILY_ISSUE_LIMIT:
        raise LetterError(429, "rate_limited",
                          "Too many letter requests today. Try again tomorrow.")
    ref.set({"day": today, "count": count + 1}, merge=True)


def _store_pdf(letter_id: str, pdf: bytes) -> str:
    from common.utils.cdn import upload_bytes
    path = f"letters/{secrets.token_urlsafe(24)}/{letter_id}.pdf"
    upload_bytes(path, pdf, "application/pdf")
    return path


def _render_final(letter_id: str, facts: Dict[str, Any], issued_at: datetime,
                  signer: letter_pdf.SignerConfig) -> bytes:
    issued_on = issued_at.astimezone(pytz.timezone(letter_pdf.ISSUE_TZ)).date()
    return letter_pdf.render_pdf(facts, letter_id=letter_id, issued_on=issued_on,
                                 signer=signer)


def _freeze(facts: Dict[str, Any]) -> Dict[str, Any]:
    """The snapshot stored on the letter: every template value, minus the
    admin-only evidence."""
    return {k: v for k, v in facts.items() if k != "evidence"}


def _create_letter(*, subject: Dict[str, Any], event_id: str, role: str,
                   facts: Dict[str, Any], show_title_line: bool,
                   exception_note: Optional[str] = None,
                   issued_by: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Issue (or return the existing) letter for (user, event, role).
    Returns (letter_doc, created)."""
    db = _db()
    user_id = subject["user_id"]
    key_ref = db.collection(LETTER_KEYS).document(_key_id(user_id, event_id, role))
    letter_id = new_letter_id()
    try:
        key_ref.create({"letter_id": letter_id, "user_id": user_id,
                        "event_id": event_id, "role": role, "created_at": _now()})
    except Exception:
        key = key_ref.get().to_dict() or {}
        doc = _letter_doc(key.get("letter_id")) if key.get("letter_id") else None
        if doc:
            return doc, False
        started = key.get("created_at")
        if isinstance(started, datetime):
            if started.tzinfo is None:
                started = pytz.utc.localize(started)
            if _now() - started < KEY_STALE_AFTER:
                # The first request is still rendering (double click).
                raise LetterError(409, "letter_in_progress",
                                  "Your letter is being prepared. Try again in a moment.")
        # A stale key without a letter = an earlier issue that failed mid-way.
        key_ref.set({"letter_id": letter_id, "created_at": _now()}, merge=True)

    signer = letter_pdf.default_signer()
    issued_at = _now()
    frozen = _freeze(facts)
    try:
        pdf = _render_final(letter_id, frozen, issued_at, signer)
        pdf_path = _store_pdf(letter_id, pdf)
    except Exception:
        key_ref.delete()
        raise
    doc = {
        "user_id": user_id,
        "email": subject.get("email"),
        "event_id": event_id,
        "role": role,
        "name_on_letter": frozen["name"],
        "show_title_line": bool(show_title_line),
        "facts": frozen,
        "template_version": lf.TEMPLATE_VERSION,
        "signer": {"name": signer.name, "title": signer.title},
        "status": STATUS_ISSUED,
        "pdf_path": pdf_path,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "issued_at": issued_at,
        "revoked_by": None, "revoked_at": None, "revoked_reason": None,
        "exception": exception_note is not None,
        "exception_note": exception_note,
        "issued_by": issued_by,
    }
    db.collection(LETTERS).document(letter_id).set(doc)
    doc["id"] = letter_id
    return doc, True


# --------------------------------------------------------------------------
# Self-serve (caller)
# --------------------------------------------------------------------------

def _reason_payload(e: lf.NotEligible) -> Dict[str, Any]:
    return {"eligible": False, "reason": e.reason}


def list_mine(propel_id: str, token_email: Optional[str]) -> Dict[str, Any]:
    """One row per (event, role) the caller applied for on a qualifying event,
    plus any letter already issued to them (e.g. an admin exception)."""
    from services.volunteers_service import get_user_volunteer_docs
    subject = subject_for_caller(propel_id, token_email)
    vols = get_user_volunteer_docs(propel_id, subject["email"])
    letters = _letters_where("user_id", propel_id)
    by_key = {(l.get("event_id"), l.get("role")): l for l in letters}

    pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for v in vols:
        role = (v.get("volunteer_type") or "").lower()
        if role in lf.ROLES and v.get("event_id"):
            key = (v["event_id"], role)
            if key not in pairs or (lf.is_selected(v) and not lf.is_selected(pairs[key])):
                pairs[key] = v
    for key in by_key:
        pairs.setdefault(key, None)

    contexts: Dict[str, lf.EventContext] = {}
    judge_ids = None
    rows = []
    for (event_id, role), vol in pairs.items():
        ctx = contexts.setdefault(event_id, lf.EventContext(event_id))
        event = ctx.event
        letter = by_key.get((event_id, role))
        if not event:
            continue
        end = lf.parse_iso_date(event.get("end_date"))
        if not letter and (not end or end < lf.LETTERS_START_DATE):
            continue
        row = {
            "event_id": event_id,
            "event_title": event.get("title") or "",
            "event_end_date": event.get("end_date") or "",
            "role": role,
            "eligible": True, "reason": None,
            "letter_id": letter["id"] if letter else None,
            "status": letter.get("status") if letter else None,
            "issued_at": _iso(letter.get("issued_at")) if letter else None,
        }
        if not letter:
            if role == "judge" and judge_ids is None:
                judge_ids = _caller_judge_ids(propel_id)
            try:
                lf.build_facts(subject, event_id, role, ctx=ctx,
                               volunteer=vol, judge_ids=judge_ids if role == "judge" else None,
                               doc_ids=subject["doc_ids"], name_on_letter="(name)")
            except lf.NotEligible as e:
                row.update(_reason_payload(e))
        rows.append(row)
    rows.sort(key=lambda r: (r["event_end_date"], r["role"]), reverse=True)
    return {
        "rows": rows,
        "name_default": lf.clean_name(subject["legal_name"]) or lf.clean_name(subject["name"]),
        "title_line": lf.title_line(subject["title"], subject["company"]),
    }


def _validated_name(name: Optional[str]) -> str:
    name = lf.clean_name(name)
    if not name:
        raise LetterError(400, lf.NAME_MISSING, "Enter your name as it should appear.")
    bad = letter_pdf.unsupported_characters(name)
    if bad:
        raise LetterError(400, "name_unsupported_characters",
                          "Your name contains characters the letter cannot print: "
                          + " ".join(bad), characters=bad)
    return name


def _caller_facts(propel_id: str, token_email: Optional[str], event_id: str, role: str,
                  name: Optional[str], show_title_line: bool):
    if role not in lf.ROLES:
        raise LetterError(400, "invalid_role", "Unknown role.")
    if not event_id or not isinstance(event_id, str):
        raise LetterError(400, "missing_fields", "event_id is required.")
    subject = subject_for_caller(propel_id, token_email)
    name = _validated_name(name) if name is not None else None
    try:
        facts = lf.build_facts(
            subject, event_id, role,
            judge_ids=_caller_judge_ids(propel_id) if role == "judge" else None,
            doc_ids=subject["doc_ids"], name_on_letter=name,
            show_title_line=bool(show_title_line))
    except lf.NotEligible as e:
        status = 404 if e.reason == lf.EVENT_NOT_FOUND else 409
        raise LetterError(status, "not_eligible", "You do not qualify for this letter.",
                          reason=e.reason)
    return subject, facts


def preview_mine(propel_id: str, token_email: Optional[str], event_id: str, role: str,
                 name: Optional[str], show_title_line: bool) -> bytes:
    _, facts = _caller_facts(propel_id, token_email, event_id, role, name, show_title_line)
    return letter_pdf.render_pdf(facts, preview=True)


def _save_legal_name(subject: Dict[str, Any], name: str):
    doc_id = subject.get("user_doc_id")
    if not doc_id or lf.clean_name(subject.get("legal_name")) == name:
        return
    try:
        _db().collection("users").document(doc_id).set({"legal_name": name}, merge=True)
    except Exception as e:
        warning(logger, "letters: could not save legal_name", exc_info=e)


def issue_mine(propel_id: str, token_email: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotent on (user, event, role): an existing issued letter is
    returned as-is (its frozen text never changes); a revoked one is refused."""
    event_id = body.get("event_id")
    role = body.get("role")
    if not event_id or role not in lf.ROLES:
        raise LetterError(400, "missing_fields", "event_id and a valid role are required.")
    _check_rate_limit(propel_id)
    existing = find_letter(propel_id, event_id, role)
    if existing:
        if existing.get("status") == STATUS_REVOKED:
            raise LetterError(409, "letter_revoked",
                              "This letter was revoked. Contact Opportunity Hack.",
                              letter_id=existing["id"])
        return {"letter_id": existing["id"], "status": existing.get("status"), "created": False}
    name = _validated_name(body.get("name_on_letter"))
    show_title_line = body.get("show_title_line") is True
    subject, facts = _caller_facts(propel_id, token_email, event_id, role, name, show_title_line)
    doc, created = _create_letter(subject=subject, event_id=event_id, role=role,
                                  facts=facts, show_title_line=show_title_line)
    _save_legal_name(subject, name)
    if created:
        info(logger, "letter issued", letter_id=doc["id"], event_id=event_id, role=role)
        _audit("letter_issued",
               f"Participation letter {doc['id']} issued: {role} at {event_id}",
               {"letter_id": doc["id"], "event_id": event_id, "role": role,
                "user_id": propel_id, "name_on_letter": doc["name_on_letter"]})
    return {"letter_id": doc["id"], "status": doc.get("status"), "created": created}


def letter_pdf_bytes(letter_id: str, propel_id: str, is_admin: bool) -> Tuple[bytes, str]:
    """(pdf bytes, filename) for the owner or an admin, issued letters only."""
    doc = _letter_doc(letter_id)
    if not doc:
        raise LetterError(404, "not_found", "Letter not found.")
    if doc.get("user_id") != propel_id and not is_admin:
        raise LetterError(403, "forbidden", "This letter belongs to someone else.")
    if doc.get("status") != STATUS_ISSUED:
        raise LetterError(410, "letter_revoked", "This letter was revoked.")
    from common.utils.cdn import download_bytes
    pdf = download_bytes(doc.get("pdf_path") or "")
    if not pdf:
        raise LetterError(500, "pdf_missing", "The letter file could not be read.")
    if hashlib.sha256(pdf).hexdigest() != doc.get("pdf_sha256"):
        warning(logger, "letters: stored PDF checksum mismatch", letter_id=letter_id)
        raise LetterError(500, "pdf_checksum_mismatch", "The letter file failed its integrity check.")
    return pdf, f"OpportunityHack-Letter-{letter_id}.pdf"


def verify(letter_id: str) -> Dict[str, Any]:
    """Public. Only what the verify page shows — no email, no user id, no
    facts beyond the served date."""
    doc = _letter_doc(letter_id)
    if not doc:
        raise LetterError(404, "not_found", "No letter has this ID.")
    facts = doc.get("facts") or {}
    return {
        "name_on_letter": doc.get("name_on_letter"),
        "role": doc.get("role"),
        "event_title": facts.get("event_title"),
        "served_date": facts.get("served_date"),
        "issued_at": _iso(doc.get("issued_at")),
        "status": doc.get("status"),
    }


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

def _event_volunteers(event_id: str) -> List[Dict[str, Any]]:
    out = []
    for snap in _db().collection("volunteers").where("event_id", "==", event_id).stream():
        d = snap.to_dict() or {}
        if (d.get("volunteer_type") or "").lower() in lf.ROLES:
            d["_doc_id"] = snap.id
            out.append(d)
    return out


def admin_list(event_id: str, status: Optional[str] = None,
               role: Optional[str] = None) -> Dict[str, Any]:
    """One row per person and role at the event, issued or not, with the
    eligibility verdict and the evidence behind it."""
    if not event_id:
        raise LetterError(400, "missing_fields", "event_id is required.")
    ctx = lf.EventContext(event_id)
    if not ctx.event:
        raise LetterError(404, "event_not_found", "Event not found.")
    letters = {(l.get("user_id"), l.get("role")): l for l in _letters_where("event_id", event_id)}
    rows, seen = [], set()
    for vol in _event_volunteers(event_id):
        vrole = vol["volunteer_type"].lower()
        if role and vrole != role:
            continue
        key = (vol.get("user_id"), vrole)
        if key in seen:
            continue
        seen.add(key)
        subject = {"user_id": vol.get("user_id"), "email": (vol.get("email") or "").lower(),
                   "name": lf.volunteer_display_name(vol), "legal_name": "", "doc_ids": set()}
        row = {
            "user_id": vol.get("user_id"), "email": vol.get("email"),
            "name": subject["name"], "role": vrole, "volunteer_id": vol["_doc_id"],
            "eligible": True, "reason": None,
            "evidence": {"score_count": None, "check_in_time": vol.get("checkInTime") or None,
                         "team_name": None, "is_selected": lf.is_selected(vol)},
        }
        try:
            facts = lf.build_facts(subject, event_id, vrole, ctx=ctx, volunteer=vol,
                                   name_on_letter=subject["name"] or "(name missing)")
            row["evidence"] = facts["evidence"]
        except lf.NotEligible as e:
            row["eligible"], row["reason"] = False, e.reason
            row["evidence"].update(e.evidence or {})
        letter = letters.pop(key, None)
        row.update(_letter_summary(letter))
        rows.append(row)
    for (uid, lrole), letter in letters.items():  # exceptions with no application
        if role and lrole != role:
            continue
        rows.append({"user_id": uid, "email": letter.get("email"),
                     "name": letter.get("name_on_letter"), "role": lrole,
                     "volunteer_id": None, "eligible": False, "reason": None,
                     "evidence": {}, **_letter_summary(letter)})
    if status == "not_issued":
        rows = [r for r in rows if not r["letter_id"]]
    elif status in (STATUS_ISSUED, STATUS_REVOKED):
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: (lf.ROLES.index(r["role"]), (r["name"] or "").lower()))
    return {"event_id": event_id, "event_title": ctx.event.get("title") or "",
            "end_date": ctx.event.get("end_date"), "rows": rows}


def _letter_summary(letter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not letter:
        return {"letter_id": None, "status": None, "issued_at": None, "exception": False,
                "facts": None, "name_on_letter": None}
    return {
        "letter_id": letter["id"], "status": letter.get("status"),
        "issued_at": _iso(letter.get("issued_at")), "exception": bool(letter.get("exception")),
        "exception_note": letter.get("exception_note"),
        "revoked_reason": letter.get("revoked_reason"),
        "facts": letter.get("facts"), "name_on_letter": letter.get("name_on_letter"),
    }


def admin_preview(user_id: str, event_id: str, role: str) -> Tuple[bytes, Dict[str, str]]:
    """The exact PDF that person gets: the stored bytes once issued, else a
    watermarked render (rules not enforced, so a sample always renders; the
    verdict comes back in headers)."""
    if not user_id or not event_id or role not in lf.ROLES:
        raise LetterError(400, "missing_fields", "user_id, event_id and role are required.")
    letter = find_letter(user_id, event_id, role)
    if letter and letter.get("status") == STATUS_ISSUED:
        pdf, _ = letter_pdf_bytes(letter["id"], user_id, is_admin=True)
        return pdf, {"X-Letter-Status": STATUS_ISSUED, "X-Letter-Id": letter["id"]}
    ctx = lf.EventContext(event_id)
    vol = next((v for v in _event_volunteers(event_id)
                if v.get("user_id") == user_id and v["volunteer_type"].lower() == role), None)
    if vol is None and not ctx.event:
        raise LetterError(404, "not_found", "No application or event found.")
    subject = subject_for_volunteer(vol or {"user_id": user_id})
    headers = {"X-Letter-Status": letter.get("status") if letter else "not_issued",
               "X-Letter-Eligible": "true", "X-Letter-Reason": ""}
    try:
        lf.build_facts(subject, event_id, role, ctx=ctx, volunteer=vol,
                       doc_ids=subject["doc_ids"])
    except lf.NotEligible as e:
        headers.update({"X-Letter-Eligible": "false", "X-Letter-Reason": e.reason})
    try:
        facts = lf.build_facts(subject, event_id, role, ctx=ctx, volunteer=vol,
                               doc_ids=subject["doc_ids"], enforce=False,
                               name_on_letter=None if (subject["legal_name"] or subject["name"])
                               else "(name missing)")
    except lf.NotEligible as e:
        raise LetterError(404, "not_found", "Event not found.", reason=e.reason)
    return letter_pdf.render_pdf(facts, preview=True), headers


def admin_revoke(letter_id: str, reason: Optional[str], admin_id: Optional[str]) -> Dict[str, Any]:
    reason = (reason or "").strip()[:MAX_NOTE_LENGTH]
    if not reason:
        raise LetterError(400, "reason_required", "A reason is required to revoke a letter.")
    doc = _letter_doc(letter_id)
    if not doc:
        raise LetterError(404, "not_found", "Letter not found.")
    if doc.get("status") == STATUS_REVOKED:
        return {"letter_id": letter_id, "status": STATUS_REVOKED}
    _db().collection(LETTERS).document(letter_id).set({
        "status": STATUS_REVOKED, "revoked_by": admin_id,
        "revoked_at": _now(), "revoked_reason": reason,
    }, merge=True)
    _audit("letter_revoked", f"Participation letter {letter_id} revoked: {reason}",
           {"letter_id": letter_id, "event_id": doc.get("event_id"), "role": doc.get("role"),
            "user_id": doc.get("user_id"), "revoked_by": admin_id})
    return {"letter_id": letter_id, "status": STATUS_REVOKED}


def _find_user_for_exception(user_id: Optional[str], email: Optional[str]) -> Dict[str, Any]:
    """{user_id, email} for the exception target: given directly, or by email
    through the user doc / an application with that email."""
    email = (email or "").strip().lower() or None
    if user_id:
        return {"user_id": user_id, "email": email}
    if not email:
        raise LetterError(400, "missing_fields", "user_id or email is required.")
    db = _db()
    for snap in db.collection("users").where("email_address", "==", email).limit(1).stream():
        pid = (snap.to_dict() or {}).get("propel_id")
        if pid:
            return {"user_id": pid, "email": email}
    for snap in db.collection("volunteers").where("email", "==", email).stream():
        pid = (snap.to_dict() or {}).get("user_id")
        if pid:
            return {"user_id": pid, "email": email}
    raise LetterError(404, "user_not_found", "No user with that email.")


def admin_exception(body: Dict[str, Any], admin_id: Optional[str]) -> Dict[str, Any]:
    """Issue a letter outside the rules (older event, missing check-in, …).
    Skips LETTERS_START_DATE and eligibility, still snapshots whatever facts
    exist, and flags the letter `exception` with the admin's note."""
    event_id, role = body.get("event_id"), body.get("role")
    note = (body.get("note") or "").strip()[:MAX_NOTE_LENGTH]
    if not event_id or role not in lf.ROLES:
        raise LetterError(400, "missing_fields", "event_id and a valid role are required.")
    if not note:
        raise LetterError(400, "note_required", "Explain why this exception is being made.")
    target = _find_user_for_exception(body.get("user_id"), body.get("email"))
    existing = find_letter(target["user_id"], event_id, role)
    if existing:
        raise LetterError(409, "already_issued", "This person already has this letter.",
                          letter_id=existing["id"], letter_status=existing.get("status"))
    ctx = lf.EventContext(event_id)
    if not ctx.event:
        raise LetterError(404, "event_not_found", "Event not found.")
    vol = next((v for v in _event_volunteers(event_id)
                if v["volunteer_type"].lower() == role
                and (v.get("user_id") == target["user_id"]
                     or (target["email"] and (v.get("email") or "").lower() == target["email"]))),
               None)
    subject = subject_for_volunteer(vol or {"user_id": target["user_id"], "email": target["email"]})
    subject["user_id"] = target["user_id"]
    try:
        facts = lf.build_facts(subject, event_id, role, ctx=ctx, volunteer=vol,
                               doc_ids=subject["doc_ids"], enforce=False)
    except lf.NotEligible as e:
        raise LetterError(400, e.reason, "This person has no name on file; ask them to "
                          "set one, or add it to their profile.")
    doc, _ = _create_letter(subject=subject, event_id=event_id, role=role, facts=facts,
                            show_title_line=False, exception_note=note, issued_by=admin_id)
    _audit("letter_exception",
           f"Participation letter {doc['id']} issued by exception: {role} at {event_id}",
           {"letter_id": doc["id"], "event_id": event_id, "role": role,
            "user_id": target["user_id"], "issued_by": admin_id, "note": note})
    return {"letter_id": doc["id"], "status": doc.get("status")}
