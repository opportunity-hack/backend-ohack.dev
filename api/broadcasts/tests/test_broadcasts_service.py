"""Tests for the Resend segment sync / broadcast / batch-send service.

Runs under ENVIRONMENT=test (see conftest / pytest env), so
_notifications_disabled() is True: nothing here may touch the network.
Resend APIs are mocked throughout.
"""
import os
import time
from unittest.mock import patch

import pytest
from mockfirestore import MockFirestore

import services.broadcasts_service as svc


ACTOR = {"propel_user_id": "test-user", "email": "greg@ohack.org"}


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_full")
    monkeypatch.setenv("RESEND_WELCOME_EMAIL_KEY", "re_test_send")
    monkeypatch.delenv("RESEND_MARKETING_CONTACT_LIMIT", raising=False)


@pytest.fixture
def db():
    mock_db = MockFirestore()
    mock_db.collection("users").add({"email_address": "alice@example.com", "name": "Alice A"})
    mock_db.collection("users").add({"email_address": "BOB@example.com", "name": "Bob B"})
    mock_db.collection("users").add({"name": "No Email"})
    mock_db.collection("leads").add({"email": "lead@example.com", "name": "Lead One"})
    mock_db.collection("leads").add({"email": "alice@example.com", "name": "Alice Lead"})
    mock_db.collection("volunteers").add({
        "email": "mentor@example.com", "name": "Mia Mentor",
        "volunteer_type": "mentor", "event_id": "2026_fall", "isSelected": True,
    })
    mock_db.collection("volunteers").add({
        "email": "pending@example.com", "name": "Pat Pending",
        "volunteer_type": "mentor", "event_id": "2026_fall", "isSelected": False,
    })
    mock_db.collection("contact_submissions").add({
        "email": "recruiter@corp.com", "firstName": "Rae", "lastName": "Recruiter",
        "inquiryType": "recruit", "receiveUpdates": True,
    })
    mock_db.collection("contact_submissions").add({
        "email": "npo@example.org", "firstName": "Nia", "lastName": "Npo",
        "inquiryType": "nonprofit", "receiveUpdates": False,
    })
    mock_db.collection("contact_submissions").add({
        "email": "RECRUITER2@corp.com", "name": "Rex Recruiter",
        "inquiryType": "Recruit", "receiveUpdates": False,
    })
    with patch.object(svc, "get_db", return_value=mock_db):
        yield mock_db


class TestNormEmail:
    def test_valid_lowercased(self):
        assert svc._norm_email(" Foo@Bar.COM ") == "foo@bar.com"

    def test_invalid(self):
        assert svc._norm_email("not-an-email") is None
        assert svc._norm_email("") is None
        assert svc._norm_email(None) is None


class TestCollectContacts:
    def test_profiles_dedupe_and_case(self, db):
        contacts, stats = svc.collect_contacts([{"type": "profiles"}])
        assert set(contacts) == {"alice@example.com", "bob@example.com"}
        assert stats["per_source"]["profiles"] == 2
        assert stats["union_total"] == 2

    def test_union_dedupes_across_sources(self, db):
        contacts, stats = svc.collect_contacts([
            {"type": "profiles"},
            {"type": "leads"},
        ])
        # alice appears in both — union removes the overlap
        assert stats["union_total"] == 3
        assert stats["overlap_removed"] == 1
        # first source wins the record; names are enriched not replaced
        assert contacts["alice@example.com"]["first_name"] == "Alice"

    def test_volunteers_selected_only(self, db):
        contacts, _ = svc.collect_contacts([
            {"type": "volunteers", "volunteer_type": "mentor",
             "event_id": "2026_fall", "selected_only": True},
        ])
        assert set(contacts) == {"mentor@example.com"}

    def test_contact_submissions_filtered_by_inquiry_type(self, db):
        contacts, stats = svc.collect_contacts([
            {"type": "contact_submissions", "inquiry_types": ["recruit"]},
        ])
        # case-insensitive on both the filter and the stored inquiryType
        assert set(contacts) == {"recruiter@corp.com", "recruiter2@corp.com"}
        assert stats["per_source"]["contact:recruit"] == 2

    def test_contact_submissions_all_types(self, db):
        contacts, _ = svc.collect_contacts([{"type": "contact_submissions"}])
        assert len(contacts) == 3

    def test_contact_submissions_opt_in_only(self, db):
        contacts, stats = svc.collect_contacts([
            {"type": "contact_submissions", "updates_opt_in_only": True},
        ])
        assert set(contacts) == {"recruiter@corp.com"}
        assert stats["per_source"]["contact:all:opted-in"] == 1

    def test_contact_submissions_bad_inquiry_types_shape(self, db):
        with pytest.raises(ValueError):
            svc.collect_contacts([
                {"type": "contact_submissions", "inquiry_types": "recruit"},
            ])

    def test_custom_emails_validated(self, db):
        contacts, stats = svc.collect_contacts([], ["good@example.com", "bad-email"])
        assert set(contacts) == {"good@example.com"}
        assert stats["custom_valid"] == 1
        assert stats["custom_invalid"] == ["bad-email"]

    def test_unknown_source_raises(self, db):
        with pytest.raises(ValueError):
            svc.collect_contacts([{"type": "nope"}])

    def test_over_limit_flag(self, db, monkeypatch):
        monkeypatch.setenv("RESEND_MARKETING_CONTACT_LIMIT", "1")
        _, stats = svc.collect_contacts([{"type": "profiles"}])
        assert stats["over_limit"] is True
        assert stats["contact_limit"] == 1


class TestPreviewSources:
    def test_preview_returns_stats(self, db):
        msg, status = svc.preview_sources({"sources": [{"type": "leads"}]})
        assert status == 200
        assert msg.stats["per_source"]["leads"] == 2

    def test_preview_bad_source_400(self, db):
        msg, status = svc.preview_sources({"sources": [{"type": "bogus"}]})
        assert status == 400


class TestSegmentSync:
    def test_sync_simulated_in_test_env(self, db):
        with patch.object(svc, "_get_or_create_segment", return_value="seg_1"), \
             patch.object(svc, "_existing_segment_emails", return_value={"alice@example.com"}), \
             patch.object(svc, "send_slack_audit"):
            msg, status = svc.start_segment_sync(
                {"segment_name": "Test Segment", "sources": [{"type": "profiles"}]},
                ACTOR,
            )
            assert status == 202
            assert msg.status == "started"
            assert msg.collected == 2

            # daemon thread — give it a beat to finish the simulated path
            for _ in range(50):
                status_msg, _ = svc.get_sync_status("seg_1")
                if status_msg.status.get("state") == "done":
                    break
                time.sleep(0.05)
            final = status_msg.status
            assert final["state"] == "done"
            assert final["simulated"] is True
            assert final["already_in_segment"] == 1
            assert final["to_add"] == 1
            assert final["added"] == 0  # simulated: no writes

    def test_sync_conflict_when_lock_held(self, db):
        svc.set_cached(svc._sync_lock_key("seg_locked"), True, ttl=60)
        try:
            msg, status = svc.start_segment_sync(
                {"segment_id": "seg_locked", "sources": [{"type": "profiles"}]},
                ACTOR,
            )
            assert status == 409
            assert msg.status == "already_running"
        finally:
            svc.delete_cached(svc._sync_lock_key("seg_locked"))

    def test_sync_requires_segment(self, db):
        _, status = svc.start_segment_sync({"sources": [{"type": "profiles"}]}, ACTOR)
        assert status == 400

    def test_stalled_detection(self, db):
        svc.set_cached(svc._sync_status_key("seg_stale"), {
            "state": "running",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }, ttl=600)
        try:
            msg, _ = svc.get_sync_status("seg_stale")
            assert msg.status["state"] == "stalled"
        finally:
            svc.delete_cached(svc._sync_status_key("seg_stale"))

    def test_status_none_when_unknown(self, db):
        msg, status = svc.get_sync_status("seg_unknown")
        assert status == 200
        assert msg.status["state"] == "none"


class TestBroadcastHtml:
    def test_unsubscribe_footer_appended(self):
        html = svc.render_broadcast_html("Hello **world**")
        assert svc.UNSUBSCRIBE_PLACEHOLDER in html
        assert "<strong>world</strong>" in html

    def test_existing_unsubscribe_not_duplicated(self):
        html = svc.render_broadcast_html(
            f"Bye [unsubscribe]({svc.UNSUBSCRIBE_PLACEHOLDER})")
        assert html.count(svc.UNSUBSCRIBE_PLACEHOLDER) == 1


class TestFromAddress:
    def test_default_allowed(self):
        addr, err = svc._resolve_from_address(None)
        assert err is None
        assert "notify.ohack.dev" in addr

    def test_disallowed_domain_rejected(self):
        addr, err = svc._resolve_from_address("Evil <x@notifs.ohack.org>")
        assert addr is None
        assert "not in the allowed list" in err

    def test_friendly_name_parsed(self):
        addr, err = svc._resolve_from_address("OHack <news@apply.ohack.dev>")
        assert err is None
        assert addr == "OHack <news@apply.ohack.dev>"


class TestCreateBroadcast:
    def test_requires_fields(self):
        _, status = svc.create_broadcast({}, ACTOR)
        assert status == 400

    def test_simulated_in_test_env(self):
        msg, status = svc.create_broadcast({
            "segment_id": "seg_1", "subject": "Hi", "body_markdown": "Hello",
        }, ACTOR)
        assert status == 200
        assert msg.simulated is True
        assert msg.broadcast["status"] == "simulated"

    def test_bad_from_rejected_before_simulation(self):
        msg, status = svc.create_broadcast({
            "segment_id": "seg_1", "subject": "Hi", "body_markdown": "Hello",
            "from_address": "x@unverified.example.com",
        }, ACTOR)
        assert status == 400


FAKE_CONTACTS = [
    {"id": "c1", "email": "a@example.com", "first_name": "A", "last_name": "",
     "unsubscribed": False, "created_at": "2026-01-01"},
    {"id": "c2", "email": "b@example.com", "first_name": "B", "last_name": "",
     "unsubscribed": True, "created_at": "2026-01-02"},
    {"id": "c3", "email": "c@example.com", "first_name": "C", "last_name": "",
     "unsubscribed": True, "created_at": "2026-01-03"},
]


class TestContacts:
    def _clean(self):
        svc.delete_cached(svc._CONTACTS_CACHE_KEY)
        svc.delete_cached(svc._PRUNE_LOCK_KEY)
        svc.delete_cached(svc._PRUNE_STATUS_KEY)

    def test_list_contacts_counts(self, monkeypatch):
        self._clean()
        monkeypatch.setenv("RESEND_MARKETING_CONTACT_LIMIT", "2")
        with patch.object(svc, "_crawl_all_contacts", return_value=FAKE_CONTACTS):
            msg, status = svc.list_contacts(force=True)
        self._clean()
        assert status == 200
        assert msg.total == 3
        assert msg.unsubscribed_count == 2
        assert msg.over_limit is True

    def test_prune_unsubscribed_targets(self):
        self._clean()
        with patch.object(svc, "_crawl_all_contacts", return_value=FAKE_CONTACTS), \
             patch.object(svc, "send_slack_audit"):
            msg, status = svc.start_contact_prune({"mode": "unsubscribed"}, ACTOR)
            assert status == 202
            assert msg.total_targets == 2
            for _ in range(50):
                status_msg, _ = svc.get_prune_status()
                if status_msg.status.get("state") == "done":
                    break
                time.sleep(0.05)
            assert status_msg.status["simulated"] is True
        self._clean()

    def test_prune_emails_intersects_known(self):
        self._clean()
        with patch.object(svc, "_crawl_all_contacts", return_value=FAKE_CONTACTS):
            msg, status = svc.start_contact_prune(
                {"mode": "emails", "emails": ["A@example.com", "nobody@x.com"]}, ACTOR)
            assert status == 202
            assert msg.total_targets == 1
            for _ in range(50):
                status_msg, _ = svc.get_prune_status()
                if status_msg.status.get("state") == "done":
                    break
                time.sleep(0.05)
        self._clean()

    def test_prune_bad_mode(self):
        self._clean()
        _, status = svc.start_contact_prune({"mode": "everything"}, ACTOR)
        assert status == 400

    def test_prune_conflict_when_running(self):
        self._clean()
        svc.set_cached(svc._PRUNE_LOCK_KEY, True, ttl=60)
        try:
            msg, status = svc.start_contact_prune({"mode": "all"}, ACTOR)
            assert status == 409
        finally:
            self._clean()

    def test_prune_empty_targets(self):
        self._clean()
        no_unsub = [c for c in FAKE_CONTACTS if not c["unsubscribed"]]
        with patch.object(svc, "_crawl_all_contacts", return_value=no_unsub):
            msg, status = svc.start_contact_prune({"mode": "unsubscribed"}, ACTOR)
        assert status == 200
        assert msg.status == "empty"
        self._clean()


class TestBatchSend:
    def test_simulated_results_in_order(self):
        with patch.object(svc, "send_slack_audit"):
            msg, status = svc.batch_send_emails({
                "subject": "Hello",
                "recipients": [
                    {"email": "a@example.com", "name": "A", "message": "hi a"},
                    {"email": "not-an-email", "name": "?", "message": "hi"},
                    {"email": "b@example.com", "name": "B",
                     "message": "scan [QRCode:https://ohack.dev]"},
                ],
            }, ACTOR)
        assert status == 200
        results = msg.results
        assert results[0]["success"] is True and results[0].get("simulated") is True
        assert results[1]["success"] is False and "invalid email" in results[1]["error"]
        assert results[2]["success"] is False and "QR-code" in results[2]["error"]
        assert msg.summary == {"total": 3, "successful": 1, "failed": 2, "simulated": True}

    def test_requires_subject_and_recipients(self):
        _, status = svc.batch_send_emails({"recipients": []}, ACTOR)
        assert status == 400
        _, status = svc.batch_send_emails({"subject": "x"}, ACTOR)
        assert status == 400

    def test_caps_request_size(self):
        recipients = [{"email": f"u{i}@example.com", "message": "hi"} for i in range(501)]
        _, status = svc.batch_send_emails({"subject": "x", "recipients": recipients}, ACTOR)
        assert status == 400
