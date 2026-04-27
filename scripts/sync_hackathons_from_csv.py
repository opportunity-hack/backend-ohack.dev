"""
Sync hackathons from a CSV export into the `hackathons` Firestore collection.

For each row in the CSV (keyed by `__id__`):
  - If the document does not exist in `hackathons`, INSERT it.
  - If it exists, compute the delta against the CSV row and UPSERT (merge) only
    the fields that differ.

CSV cell encoding produced by the export:
  - `__DocumentReference__<collection>/<doc_id>`  -> Firestore DocumentReference
  - `__Timestamp__<iso8601>`                     -> Python datetime
  - JSON object/array literals in: constraints, countdowns, donation_current,
    donation_goals, links, nonprofits, teams, visible_problem_statements

Usage:
  python scripts/sync_hackathons_from_csv.py /path/to/_hackathons_changes_only.csv --dry-run
  python scripts/sync_hackathons_from_csv.py /path/to/_hackathons_changes_only.csv --apply
"""

import argparse
import csv
import datetime
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db  # noqa: E402
from google.cloud.firestore import DocumentReference  # noqa: E402


COLLECTION = "hackathons"

# Cells that should be parsed as JSON when non-empty.
JSON_FIELDS = {
    "constraints",
    "countdowns",
    "donation_current",
    "donation_goals",
    "links",
    "nonprofits",
    "teams",
    "visible_problem_statements",
}

# Skip writing these CSV columns to the doc body.
SKIP_FIELDS = {"__id__"}

DOC_REF_PREFIX = "__DocumentReference__"
TIMESTAMP_PREFIX = "__Timestamp__"


def decode_value(value, db):
    """Recursively decode the export sentinels into native Firestore types."""
    if isinstance(value, str):
        if value.startswith(DOC_REF_PREFIX):
            path = value[len(DOC_REF_PREFIX):]
            return db.document(path)
        if value.startswith(TIMESTAMP_PREFIX):
            iso = value[len(TIMESTAMP_PREFIX):].replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(iso)
        return value
    if isinstance(value, list):
        return [decode_value(v, db) for v in value]
    if isinstance(value, dict):
        return {k: decode_value(v, db) for k, v in value.items()}
    return value


def parse_cell(field, raw, db):
    """Parse a single CSV cell into the value we will store in Firestore."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        # Empty string: keep as empty string for scalar fields, [] / {} for known JSON shapes
        if field in {"nonprofits", "teams", "links", "countdowns", "visible_problem_statements"}:
            return []
        if field in {"constraints", "donation_current", "donation_goals"}:
            return {}
        return ""

    if field in JSON_FIELDS:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON for field {field!r}: {e}\nValue: {raw!r}")
        return decode_value(parsed, db)

    return decode_value(raw, db)


def row_to_doc(row, db):
    doc = {}
    for field, raw in row.items():
        if field in SKIP_FIELDS:
            continue
        doc[field] = parse_cell(field, raw, db)
    return doc


def normalize_for_compare(value):
    """Make values comparable by converting refs/datetimes to stable strings."""
    if isinstance(value, DocumentReference):
        return f"ref::{value.path}"
    if isinstance(value, datetime.datetime):
        # Drop tz info inconsistencies by converting to UTC isoformat
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return f"ts::{value.isoformat()}"
    if isinstance(value, list):
        return [normalize_for_compare(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize_for_compare(v) for k, v in value.items()}
    return value


def is_empty(v):
    return v == "" or v == [] or v == {} or v is None


def compute_delta(existing, incoming, skip_empty_additions=True):
    """Return dict of fields where incoming differs from existing.

    If skip_empty_additions is True, fields that are missing on the existing
    doc and whose incoming value is empty ('', [], {}, None) are NOT included
    in the delta. This avoids polluting docs with default/empty values that
    aren't real changes.
    """
    delta = {}
    for k, v in incoming.items():
        existing_v = existing.get(k, _MISSING)
        if existing_v is _MISSING:
            if skip_empty_additions and is_empty(v):
                continue
            delta[k] = v
            continue
        if normalize_for_compare(existing_v) != normalize_for_compare(v):
            delta[k] = v
    return delta


_MISSING = object()


def short(v, n=80):
    s = repr(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to the hackathons CSV")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="(default) Only report what would change; no writes.")
    g.add_argument("--apply", action="store_true",
                   help="Actually write inserts/updates to Firestore.")
    args = parser.parse_args()

    apply = args.apply
    dry_run = not apply

    db = get_db()

    with open(args.csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Loaded {len(rows)} rows from {args.csv_path}")
    print(f"Mode: {'APPLY (writes will happen)' if apply else 'DRY RUN (no writes)'}")
    print("=" * 80)

    inserts = []
    updates = []
    unchanged = []

    coll = db.collection(COLLECTION)

    for i, row in enumerate(rows, start=1):
        doc_id = (row.get("__id__") or "").strip()
        if not doc_id:
            print(f"[{i:02d}] SKIP: row has no __id__")
            continue

        title = row.get("title", "")
        event_id = row.get("event_id", "")
        ref = coll.document(doc_id)
        snap = ref.get()

        try:
            incoming = row_to_doc(row, db)
        except ValueError as e:
            print(f"[{i:02d}] ERROR parsing id={doc_id} ({title!r}): {e}")
            continue

        if not snap.exists:
            inserts.append((doc_id, title, event_id, incoming))
            print(f"[{i:02d}] INSERT  id={doc_id}  event_id={event_id!r}  title={title!r}")
            if apply:
                ref.set(incoming)
            continue

        existing = snap.to_dict() or {}
        delta = compute_delta(existing, incoming)
        if not delta:
            unchanged.append((doc_id, title))
            print(f"[{i:02d}] SAME    id={doc_id}  event_id={event_id!r}  title={title!r}")
            continue

        updates.append((doc_id, title, event_id, delta))
        print(f"[{i:02d}] UPDATE  id={doc_id}  event_id={event_id!r}  title={title!r}")
        for k, v in delta.items():
            old = existing.get(k, _MISSING)
            old_repr = "<missing>" if old is _MISSING else short(old)
            print(f"        - {k}: {old_repr}  ->  {short(v)}")
        if apply:
            ref.set(delta, merge=True)

    print("=" * 80)
    print(f"Summary: {len(inserts)} insert(s), {len(updates)} update(s), {len(unchanged)} unchanged")
    if dry_run:
        print("Dry run only. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
