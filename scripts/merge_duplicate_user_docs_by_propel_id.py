#!/usr/bin/env python3
"""
Merge duplicate `users` Firestore docs that share a propel_id.

DRY-RUN BY DEFAULT. Pass --apply to actually merge + delete.

Why this exists
---------------
PropelAuth links accounts by email across OAuth providers (Slack, Google, ...)
so the same person has ONE propel_id. But the backend previously stored each
provider's `user_id` (e.g. `oauth2|slack|...` vs `oauth2|google-oauth2|...`)
on a *separate* `users` doc when the user first signed in via the second
provider. The result: two Firestore docs for the same person, with their
volunteering history / teams / badges split across both.

The save_user fallback added in services/users_service.py prevents *new*
duplicates from being created. This script cleans up the ones that already
exist.

What it does
------------
Groups docs by `propel_id`. For each group with >1 doc:
  - Picks the "winner" (most history, oldest last_login).
  - Merges non-empty fields from the loser(s) into the winner:
      * scalar fields (name, nickname, email_address, profile_image,
        github, role, company, ...) - winner's value wins if non-empty,
        otherwise inherits from loser
      * last_login                  - most recent of all docs
      * volunteering                - union, deduped by (timestamp, reason)
      * teams / hackathons / badges - union of DocumentReferences, deduped by path
      * history                     - dict merge, winner's keys win
      * privacy_settings            - dict merge, winner's keys win
      * user_id                     - winner's (anchor identity)
  - Deletes the loser doc(s).

Docs without a propel_id are ignored (single-provider users we can't link).

Usage
-----
  cd backend-ohack.dev

  # report only (default - read-only):
  python scripts/merge_duplicate_user_docs_by_propel_id.py

  # filter to a single propel_id while debugging:
  python scripts/merge_duplicate_user_docs_by_propel_id.py --propel-id abc123

  # write JSON report to a file:
  python scripts/merge_duplicate_user_docs_by_propel_id.py --report-out /tmp/dupes.json

  # actually merge + delete:
  python scripts/merge_duplicate_user_docs_by_propel_id.py --apply
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db


# Scalar fields where the winner's non-empty value wins, falling back to a
# loser's non-empty value. Lists/dicts are handled separately below.
_SCALAR_FIELDS = [
    "email_address",
    "profile_image",
    "name",
    "nickname",
    "expertise",
    "education",
    "shirt_size",
    "github",
    "role",
    "company",
    "why",
    "linkedin_url",
    "instagram_url",
    "street_address",
    "street_address_2",
    "city",
    "state",
    "postal_code",
    "country",
    "want_stickers",
]


def _ref_path(ref):
    """Stable identity for a DocumentReference / dict / string."""
    if ref is None:
        return None
    if hasattr(ref, "path"):
        return ref.path
    if isinstance(ref, dict) and "id" in ref:
        return ref["id"]
    return str(ref)


def _score_doc(d):
    """Higher = more history. Used to pick the winner."""
    vol = len(d.get("volunteering") or [])
    teams = len(d.get("teams") or [])
    hacks = len(d.get("hackathons") or [])
    badges = len(d.get("badges") or [])
    scalar_filled = sum(1 for f in _SCALAR_FIELDS if (d.get(f) or "") not in ("", None))
    # Heavy weight on volunteering and teams since those are the user-visible loss.
    return (vol * 10) + (teams * 5) + (hacks * 3) + badges + scalar_filled


def _parse_iso(ts):
    if not ts:
        return None
    s = str(ts).rstrip("Z")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _pick_winner(docs):
    """docs = list of (doc_id, dict). Returns the winner doc tuple."""
    def key(t):
        _, d = t
        last_login = _parse_iso(d.get("last_login")) or datetime.min
        # Higher score first, then OLDER last_login (older = more established)
        return (-_score_doc(d), last_login, t[0])
    return sorted(docs, key=key)[0]


def _dedupe_volunteering(entries):
    seen = set()
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        key = (e.get("timestamp"), e.get("reason"), e.get("finalHours"), e.get("commitmentHours"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _dedupe_refs(refs):
    seen = set()
    out = []
    for r in refs or []:
        p = _ref_path(r)
        if p in seen:
            continue
        seen.add(p)
        out.append(r)
    return out


def _merge_dicts(winner_d, losers_d):
    """Shallow dict merge: winner keys win, losers fill in missing keys."""
    merged = {}
    for d in losers_d:
        if isinstance(d, dict):
            merged.update(d)
    if isinstance(winner_d, dict):
        merged.update(winner_d)
    return merged


def _max_last_login(docs):
    best = None
    for _, d in docs:
        ts = _parse_iso(d.get("last_login"))
        if ts and (best is None or ts > best):
            best = ts
            best_raw = d.get("last_login")
    return best_raw if best else (docs[0][1].get("last_login"))


def _build_merged_payload(winner, losers):
    _, w = winner
    payload = {}

    # Scalar fields: keep winner's non-empty, else first non-empty loser
    for f in _SCALAR_FIELDS:
        if (w.get(f) or "") not in ("", None):
            continue
        for _, l in losers:
            if (l.get(f) or "") not in ("", None):
                payload[f] = l[f]
                break

    # last_login: most recent across all
    payload["last_login"] = _max_last_login([winner] + losers)

    # volunteering: union, deduped
    all_vol = list(w.get("volunteering") or [])
    for _, l in losers:
        all_vol.extend(l.get("volunteering") or [])
    payload["volunteering"] = _dedupe_volunteering(all_vol)

    # teams / hackathons / badges: union by ref path
    for list_field in ("teams", "hackathons", "badges"):
        combined = list(w.get(list_field) or [])
        for _, l in losers:
            combined.extend(l.get(list_field) or [])
        payload[list_field] = _dedupe_refs(combined)

    # history / privacy_settings: shallow dict merge (winner wins)
    payload["history"] = _merge_dicts(w.get("history"), [l.get("history") for _, l in losers])
    payload["privacy_settings"] = _merge_dicts(
        w.get("privacy_settings"), [l.get("privacy_settings") for _, l in losers]
    )

    # user_id intentionally NOT changed - keep winner's anchor identity
    return payload


def _summarize_doc(doc_id, d):
    return {
        "id": doc_id,
        "user_id": d.get("user_id"),
        "email": d.get("email_address"),
        "name": d.get("name"),
        "last_login": d.get("last_login"),
        "score": _score_doc(d),
        "volunteering_count": len(d.get("volunteering") or []),
        "teams_count": len(d.get("teams") or []),
        "hackathons_count": len(d.get("hackathons") or []),
        "badges_count": len(d.get("badges") or []),
    }


def _scan(db, filter_propel_id=None):
    """Group all users by propel_id. Returns dict[propel_id] -> [(doc_id, dict)]."""
    groups = defaultdict(list)
    query = db.collection("users")
    if filter_propel_id:
        query = query.where("propel_id", "==", filter_propel_id)
    for snap in query.stream():
        d = snap.to_dict() or {}
        pid = d.get("propel_id")
        if not pid:
            continue
        groups[pid].append((snap.id, d))
    return groups


def _apply_merge(db, winner, losers, payload):
    winner_id, _ = winner
    # Apply the merge first so we never delete a loser before its data lands
    # on the winner. Firestore .update() is atomic for the doc; if it fails,
    # we abort before deleting.
    db.collection("users").document(winner_id).update(payload)
    for loser_id, _ in losers:
        db.collection("users").document(loser_id).delete()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Actually merge + delete. Default is dry-run.")
    p.add_argument("--propel-id", help="Limit to a single propel_id (handy for one-off cleanup).")
    p.add_argument("--report-out", help="Write the full JSON report to this path.")
    p.add_argument("--verbose", "-v", action="store_true", help="Print every group, not just dupes.")
    args = p.parse_args()

    print(f"Mode: {'APPLY (will mutate Firestore)' if args.apply else 'DRY-RUN (read-only)'}")
    if args.propel_id:
        print(f"Filter: propel_id == {args.propel_id}")

    db = get_db()
    groups = _scan(db, filter_propel_id=args.propel_id)

    total_users = sum(len(v) for v in groups.values())
    dupe_groups = {pid: docs for pid, docs in groups.items() if len(docs) > 1}

    print(f"Scanned {total_users} users with propel_id across {len(groups)} unique propel_ids.")
    print(f"Found {len(dupe_groups)} propel_ids with >1 doc.")

    report = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "total_users_with_propel_id": total_users,
        "unique_propel_ids": len(groups),
        "duplicate_groups": [],
    }

    merged_count = 0
    deleted_count = 0
    errors = []

    for pid, docs in sorted(dupe_groups.items()):
        winner = _pick_winner(docs)
        losers = [t for t in docs if t[0] != winner[0]]
        payload = _build_merged_payload(winner, losers)

        group_report = {
            "propel_id": pid,
            "winner": _summarize_doc(*winner),
            "losers": [_summarize_doc(*l) for l in losers],
            "merged_counts": {
                "volunteering": len(payload["volunteering"]),
                "teams": len(payload["teams"]),
                "hackathons": len(payload["hackathons"]),
                "badges": len(payload["badges"]),
            },
            "scalar_fields_inherited": sorted(
                k for k in payload.keys() if k in _SCALAR_FIELDS
            ),
        }

        print()
        print(f"propel_id: {pid}")
        print(f"  WINNER  : {group_report['winner']}")
        for l in group_report["losers"]:
            print(f"  loser   : {l}")
        print(f"  merged  : {group_report['merged_counts']}")
        if group_report["scalar_fields_inherited"]:
            print(f"  inherit : {group_report['scalar_fields_inherited']}")

        if args.apply:
            try:
                _apply_merge(db, winner, losers, payload)
                merged_count += 1
                deleted_count += len(losers)
                group_report["applied"] = True
            except Exception as e:
                group_report["applied"] = False
                group_report["error"] = repr(e)
                errors.append((pid, repr(e)))
                print(f"  ERROR  : {e!r}")
        else:
            group_report["applied"] = False

        report["duplicate_groups"].append(group_report)

    if args.verbose:
        for pid, docs in sorted(groups.items()):
            if len(docs) == 1:
                print(f"\nsingle    propel_id={pid}: {_summarize_doc(*docs[0])}")

    print()
    print("=" * 70)
    if args.apply:
        print(f"Applied: merged {merged_count} groups, deleted {deleted_count} loser docs.")
        if errors:
            print(f"Errors : {len(errors)} groups failed - see report.")
    else:
        print(f"DRY-RUN: would merge {len(dupe_groups)} groups and delete "
              f"{sum(len(d) - 1 for d in dupe_groups.values())} loser docs.")
        print("Re-run with --apply to write changes.")

    if args.report_out:
        with open(args.report_out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report written to {args.report_out}")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
