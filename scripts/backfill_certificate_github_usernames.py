#!/usr/bin/env python3
"""
Backfill script to stamp `github_username` on existing certificate docs.

New certificates get github_username at generation time
(api/certificates/certificate_service.py::generate_certificate); this script
computes it for pre-existing docs so GET /api/certificates?github=<username>
(and the portfolio page) can find them.

Usage:
    python scripts/backfill_certificate_github_usernames.py            # dry-run
    python scripts/backfill_certificate_github_usernames.py --apply
"""

import argparse
import os
import sys

from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import from project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# NOTE: user-facing output uses print(), not logging — importing the app
# modules below reconfigures logging (common/log) and disables loggers
# created here, which made an earlier version of this script appear to do
# nothing at all.


def main():
    parser = argparse.ArgumentParser(description="Stamp github_username on certificate docs")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Log what would change (default)")
    args = parser.parse_args()
    apply_changes = args.apply and not args.dry_run

    from common.utils.firebase import get_db
    from api.certificates.certificate_service import _extract_github_username

    db = get_db()
    docs = list(db.collection("certificates").stream())
    print(f"Scanning {len(docs)} certificate docs (mode: {'APPLY' if apply_changes else 'DRY-RUN'})")

    # Second-chance matching: many certs carry a personal git email (not the
    # GitHub noreply form). Join those against users.email_address -> github.
    email_to_github = {}
    try:
        for udoc in db.collection("users").where("github", ">", "").stream():
            u = udoc.to_dict() or {}
            email = (u.get("email_address") or "").strip().lower()
            github = (u.get("github") or "").strip().lower()
            if email and github:
                email_to_github[email] = github
        print(f"Loaded {len(email_to_github)} users with a github username for email matching")
    except Exception as e:
        print(f"WARNING could not load users for email matching: {e}")

    stamped = 0
    already = 0
    unparseable = []
    for doc in docs:
        cert = doc.to_dict() or {}
        if cert.get("github_username"):
            already += 1
            continue

        username = _extract_github_username(cert.get("author_email"), cert.get("author_name"))
        if not username:
            username = email_to_github.get((cert.get("author_email") or "").strip().lower())
        if not username:
            unparseable.append({
                "doc_id": doc.id,
                "author_name": cert.get("author_name"),
                "author_email": cert.get("author_email"),
            })
            continue

        stamped += 1
        if apply_changes:
            doc.reference.update({"github_username": username})
            print(f"Stamped {doc.id}: github_username={username}")
        else:
            print(f"[dry-run] Would stamp {doc.id}: github_username={username}")

    print(f"Done. stamped={stamped} already_stamped={already} unparseable={len(unparseable)}")
    for entry in unparseable:
        print(f"WARNING unparseable cert {entry['doc_id']}: name={entry['author_name']!r} email={entry['author_email']!r}")

    if not apply_changes:
        print("Dry-run only — re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
