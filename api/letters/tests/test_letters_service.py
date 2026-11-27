"""letters_service against an in-memory Firestore: issue (idempotent, rate
limited), stream (owner/admin, same bytes), verify (no extra fields),
/mine rows, admin list / revoke / exception."""
import os
from datetime import datetime

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytz

from api.letters import letter_facts as lf
from api.letters import letters_service as svc
from api.letters.tests._fake_db import FakeDb

FALL = {"event_id": "fall26", "title": "Opportunity Hack Fall 2026",
        "start_date": "2026-11-14", "end_date": "2026-11-15",
        "timezone": "America/Phoenix", "location": "Tempe, AZ"}
SPRING = {"event_id": "spring26", "title": "Opportunity Hack Spring 2026",
          "start_date": "2026-03-14", "end_date": "2026-03-15",
          "timezone": "America/Phoenix", "location": "Tempe, AZ"}
NOW = pytz.utc.localize(datetime(2026, 11, 27, 18, 0))


@pytest.fixture
def env(monkeypatch):
    db = FakeDb()
    events = {"fall26": FALL, "spring26": SPRING}
    blobs, audits = {}, []

    monkeypatch.setattr(svc, "_db", lambda: db)
    monkeypatch.setattr(lf, "_db", lambda: db)
    monkeypatch.setattr(svc, "_now", lambda: NOW)
    monkeypatch.setattr(svc, "_caller_judge_ids", lambda pid: [pid])
    monkeypatch.setattr(svc, "_audit", lambda action, message, payload: audits.append(action))

    def event_prop(self):
        if not self._event_loaded:
            self._event_loaded = True
            self._event = dict(events[self.event_id]) if self.event_id in events else None
        return self._event
    monkeypatch.setattr(lf.EventContext, "event", property(event_prop))
    monkeypatch.setattr(lf.EventContext, "scores", property(
        lambda self: [d for d in db.docs("judge_scores").values()
                      if d["event_id"] == self.event_id and not d.get("is_draft")]))

    real_build = lf.build_facts
    monkeypatch.setattr(lf, "build_facts", lambda *a, **k: real_build(*a, **dict(k, now=k.get("now") or NOW)))

    def volunteer_docs(user_id=None, email=None):
        out = []
        for doc_id, d in db.docs("volunteers").items():
            if (user_id and d.get("user_id") == user_id) or (email and d.get("email") == email):
                out.append(dict(d, _doc_id=doc_id))
        return out
    import services.volunteers_service as vs
    monkeypatch.setattr(vs, "get_user_volunteer_docs", volunteer_docs)

    def subject(propel_id, token_email):
        raw = db.docs("users").get("udoc-" + propel_id, {})
        return {"user_id": propel_id, "email": token_email, "user_doc_id": "udoc-" + propel_id,
                "doc_ids": {"udoc-" + propel_id}, "name": raw.get("name", ""),
                "legal_name": raw.get("legal_name", ""), "title": raw.get("role", ""),
                "company": raw.get("company", "")}
    monkeypatch.setattr(svc, "subject_for_caller", subject)

    import common.utils.cdn as cdn
    monkeypatch.setattr(cdn, "upload_bytes",
                        lambda path, data, ct, **_: blobs.__setitem__(path, data) or path)
    monkeypatch.setattr(cdn, "download_bytes", lambda path: blobs.get(path))

    db.put("users", "udoc-mentor-1", {"name": "Ada Lovelace", "propel_id": "mentor-1",
                                      "email_address": "ada@example.com", "role": "Engineer",
                                      "company": "Analytical Co"})
    db.put("volunteers", "v1", {"user_id": "mentor-1", "email": "ada@example.com",
                                "volunteer_type": "mentor", "event_id": "fall26",
                                "isSelected": True, "checkInTime": "2026-11-14T09:00:00-07:00",
                                "name": "Ada Lovelace"})
    db.put("volunteers", "v2", {"user_id": "mentor-1", "email": "ada@example.com",
                                "volunteer_type": "judge", "event_id": "fall26",
                                "isSelected": True, "name": "Ada Lovelace"})
    db.put("volunteers", "v3", {"user_id": "mentor-1", "email": "ada@example.com",
                                "volunteer_type": "mentor", "event_id": "spring26",
                                "isSelected": True, "checkInTime": "2026-03-14T09:00:00-07:00"})
    db.put("volunteers", "v4", {"user_id": "other-1", "email": "bo@example.com",
                                "volunteer_type": "volunteer", "event_id": "fall26",
                                "isSelected": True, "name": "Bo Diddley"})
    return {"db": db, "blobs": blobs, "audits": audits}


def issue(**body):
    payload = {"event_id": "fall26", "role": "mentor", "name_on_letter": "Ada King"}
    payload.update(body)
    return svc.issue_mine("mentor-1", "ada@example.com", payload)


def test_issue_creates_one_letter_and_is_idempotent(env):
    first = issue(show_title_line=True)
    assert first["created"] is True
    assert svc.is_letter_id(first["letter_id"])
    doc = env["db"].docs("letters")[first["letter_id"]]
    assert doc["status"] == "issued"
    assert doc["name_on_letter"] == "Ada King"
    assert doc["facts"]["title_line"] == "Engineer, Analytical Co"
    assert "evidence" not in doc["facts"]
    assert doc["exception"] is False
    assert doc["signer"] == {"name": "Greg Vannoni", "title": "President, Opportunity Hack, Inc."}
    assert doc["pdf_path"].startswith("letters/") and doc["pdf_path"].endswith(".pdf")
    assert env["db"].docs("users")["udoc-mentor-1"]["legal_name"] == "Ada King"
    assert env["audits"] == ["letter_issued"]

    again = issue(name_on_letter="Someone Else")
    assert again == {"letter_id": first["letter_id"], "status": "issued", "created": False}
    assert len(env["db"].docs("letters")) == 1
    assert len(env["blobs"]) == 1


def test_letter_in_progress_is_not_duplicated(env):
    key = svc._key_id("mentor-1", "fall26", "mentor")
    env["db"].put("letter_keys", key, {"letter_id": "PENDINGPENDI", "created_at": NOW})
    with pytest.raises(svc.LetterError) as exc:
        issue()
    assert exc.value.code == "letter_in_progress"
    assert env["db"].docs("letters") == {}


def test_rate_limit_ten_per_day(env):
    for _ in range(10):
        issue()
    with pytest.raises(svc.LetterError) as exc:
        issue()
    assert exc.value.status == 429


def test_not_eligible_is_refused_with_reason(env):
    with pytest.raises(svc.LetterError) as exc:
        issue(role="judge")  # approved judge, no scores
    assert exc.value.status == 409
    assert exc.value.body()["reason"] == lf.NO_SCORES
    with pytest.raises(svc.LetterError) as exc:
        issue(event_id="spring26")
    assert exc.value.body()["reason"] == lf.BEFORE_START_DATE


def test_judge_with_scores_is_issued(env):
    env["db"].put("judge_scores", "s1", {"judge_id": "mentor-1", "team_id": "t1",
                                         "event_id": "fall26", "is_draft": False,
                                         "submitted_at": "2026-11-15T22:00:00Z"})
    assert issue(role="judge")["created"]


def test_bad_names_are_refused(env):
    for name, code in (("", lf.NAME_MISSING), ("   ", lf.NAME_MISSING),
                       ("Ada 🙂", "name_unsupported_characters")):
        with pytest.raises(svc.LetterError) as exc:
            issue(name_on_letter=name)
        assert exc.value.code == code


def test_pdf_owner_and_admin_only_and_same_bytes(env):
    letter_id = issue()["letter_id"]
    a, filename = svc.letter_pdf_bytes(letter_id, "mentor-1", is_admin=False)
    b, _ = svc.letter_pdf_bytes(letter_id, "admin-9", is_admin=True)
    assert a == b and a.startswith(b"%PDF")
    assert filename == f"OpportunityHack-Letter-{letter_id}.pdf"
    with pytest.raises(svc.LetterError) as exc:
        svc.letter_pdf_bytes(letter_id, "other-1", is_admin=False)
    assert exc.value.status == 403
    with pytest.raises(svc.LetterError) as exc:
        svc.letter_pdf_bytes("ZZZZZZZZZZZZ", "mentor-1", is_admin=False)
    assert exc.value.status == 404


def test_tampered_pdf_is_refused(env):
    letter_id = issue()["letter_id"]
    path = env["db"].docs("letters")[letter_id]["pdf_path"]
    env["blobs"][path] = b"%PDF-tampered"
    with pytest.raises(svc.LetterError) as exc:
        svc.letter_pdf_bytes(letter_id, "mentor-1", is_admin=False)
    assert exc.value.code == "pdf_checksum_mismatch"


def test_verify_returns_only_public_fields(env):
    letter_id = issue()["letter_id"]
    result = svc.verify(letter_id)
    assert set(result) == {"name_on_letter", "role", "event_title", "served_date",
                           "issued_at", "status"}
    assert result["name_on_letter"] == "Ada King"
    assert result["served_date"] == "November 14, 2026"
    assert result["issued_at"] == "2026-11-27T18:00:00Z"
    for bad in ("ZZZZZZZZZZZZ", "short", "../../etc", "abcdefghijkl"):
        with pytest.raises(svc.LetterError) as exc:
            svc.verify(bad)
        assert exc.value.status == 404


def test_revoke_then_issue_and_download_are_refused(env):
    letter_id = issue()["letter_id"]
    with pytest.raises(svc.LetterError) as exc:
        svc.admin_revoke(letter_id, "  ", "admin-9")
    assert exc.value.code == "reason_required"
    assert svc.admin_revoke(letter_id, "Issued in error", "admin-9")["status"] == "revoked"
    doc = env["db"].docs("letters")[letter_id]
    assert doc["revoked_by"] == "admin-9" and doc["revoked_reason"] == "Issued in error"
    assert svc.verify(letter_id)["status"] == "revoked"
    with pytest.raises(svc.LetterError) as exc:
        svc.letter_pdf_bytes(letter_id, "mentor-1", is_admin=False)
    assert exc.value.status == 410
    with pytest.raises(svc.LetterError) as exc:
        issue()
    assert exc.value.code == "letter_revoked"
    assert "letter_revoked" in env["audits"]


def test_mine_rows(env):
    result = svc.list_mine("mentor-1", "ada@example.com")
    rows = {(r["event_id"], r["role"]): r for r in result["rows"]}
    assert set(rows) == {("fall26", "mentor"), ("fall26", "judge")}  # spring26 is pre-start
    assert rows[("fall26", "mentor")]["eligible"] is True
    assert rows[("fall26", "judge")] ["reason"] == lf.NO_SCORES
    assert result["name_default"] == "Ada Lovelace"
    assert result["title_line"] == "Engineer, Analytical Co"

    letter_id = issue()["letter_id"]
    rows = {(r["event_id"], r["role"]): r for r in svc.list_mine("mentor-1", None)["rows"]}
    assert rows[("fall26", "mentor")]["letter_id"] == letter_id
    assert rows[("fall26", "mentor")]["status"] == "issued"


def test_exception_for_older_event_shows_up_in_mine(env):
    with pytest.raises(svc.LetterError) as exc:
        svc.admin_exception({"email": "ada@example.com", "event_id": "spring26",
                             "role": "mentor"}, "admin-9")
    assert exc.value.code == "note_required"
    result = svc.admin_exception({"email": "ada@example.com", "event_id": "spring26",
                                  "role": "mentor", "note": "Spring 2026 mentor"}, "admin-9")
    doc = env["db"].docs("letters")[result["letter_id"]]
    assert doc["exception"] is True and doc["exception_note"] == "Spring 2026 mentor"
    assert doc["user_id"] == "mentor-1" and doc["issued_by"] == "admin-9"
    assert doc["facts"]["served_date"] == "March 14, 2026"
    assert "letter_exception" in env["audits"]
    rows = {(r["event_id"], r["role"]) for r in svc.list_mine("mentor-1", None)["rows"]}
    assert ("spring26", "mentor") in rows
    with pytest.raises(svc.LetterError) as exc:
        svc.admin_exception({"user_id": "mentor-1", "event_id": "spring26",
                             "role": "mentor", "note": "again"}, "admin-9")
    assert exc.value.status == 409
    with pytest.raises(svc.LetterError) as exc:
        svc.admin_exception({"email": "nobody@example.com", "event_id": "spring26",
                             "role": "mentor", "note": "x"}, "admin-9")
    assert exc.value.code == "user_not_found"


def test_admin_list_with_evidence_and_filters(env):
    issue()
    result = svc.admin_list("fall26")
    rows = {(r["user_id"], r["role"]): r for r in result["rows"]}
    assert rows[("mentor-1", "mentor")]["status"] == "issued"
    assert rows[("mentor-1", "mentor")]["evidence"]["check_in_time"]
    assert rows[("mentor-1", "judge")]["reason"] == lf.NO_SCORES
    assert rows[("mentor-1", "judge")]["evidence"]["score_count"] == 0
    assert rows[("other-1", "volunteer")]["reason"] == lf.NO_CHECKIN
    assert [r["role"] for r in svc.admin_list("fall26", status="issued")["rows"]] == ["mentor"]
    assert {r["role"] for r in svc.admin_list("fall26", status="not_issued")["rows"]} \
        == {"judge", "volunteer"}
    assert [r["role"] for r in svc.admin_list("fall26", role="judge")["rows"]] == ["judge"]
    with pytest.raises(svc.LetterError):
        svc.admin_list("nope")


def test_admin_preview_stored_or_watermarked(env, monkeypatch):
    monkeypatch.setattr(svc, "subject_for_volunteer", lambda vol: {
        "user_id": vol.get("user_id"), "email": vol.get("email"), "user_doc_id": None,
        "doc_ids": set(), "name": vol.get("name") or "", "legal_name": "", "title": "",
        "company": ""})
    pdf, headers = svc.admin_preview("other-1", "fall26", "volunteer")
    assert pdf.startswith(b"%PDF")
    assert headers["X-Letter-Eligible"] == "false"
    assert headers["X-Letter-Reason"] == lf.NO_CHECKIN
    letter_id = issue()["letter_id"]
    stored, _ = svc.letter_pdf_bytes(letter_id, "mentor-1", is_admin=False)
    pdf, headers = svc.admin_preview("mentor-1", "fall26", "mentor")
    assert pdf == stored and headers["X-Letter-Id"] == letter_id


def test_preview_mine_is_owner_facts_only(env):
    pdf = svc.preview_mine("mentor-1", "ada@example.com", "fall26", "mentor", "Ada", True)
    assert pdf.startswith(b"%PDF")
    with pytest.raises(svc.LetterError) as exc:
        svc.preview_mine("other-1", "bo@example.com", "fall26", "mentor", "Bo", False)
    assert exc.value.body()["reason"] == lf.NOT_SELECTED


def test_letter_ids():
    ids = {svc.new_letter_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(svc.is_letter_id(i) for i in ids)
