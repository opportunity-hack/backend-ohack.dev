# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- Install dependencies: `pip install -r requirements.txt`
- Run the app: `flask run` (or `gunicorn api.wsgi:app --log-file=- --log-level debug --preload --workers 1`)
- Run tests: `pytest`
- Run a single test: `pytest path/to/test_file.py::test_function_name`
- Run linting: `pylint api/ common/ db/ model/ services/`
- Python environment: Miniconda with `conda activate py39_ohack_backend` (Python 3.9.13)

## Architecture

A note about this codebase: This was originally taken from Auth0 and messages_views.py and messages_service.py were the original files we built from.  Over time we have created a better structure for this backend service.  Please do not update these two files anymore, you should consider the other code that exists first, and then add a new _views.py and a new _service.py if you're unable to find a good place to update the code as it relates to the file.  If you find code in these two "messages" files that you plan to update, it's encouraged to migrate that code elsewhere to help iteratively move away from messages_views and messages_service.

### Flask app with Blueprint-based API modules
The app is created via `api/__init__.py:create_app()`. Each API domain is a Blueprint registered there. The WSGI entrypoint is `api/wsgi.py`.

### API module pattern
Each API domain lives in `api/<domain>/` with a consistent structure:
- `<domain>_views.py` — Flask Blueprint with route definitions. Routes use `@cross_origin()` and PropelAuth's `@auth.require_user` for authentication.
- `<domain>_service.py` — Business logic called by views. Some older services live in `services/` at the top level (hearts, users, volunteers, etc.).
- `tests/` — Tests for that domain.

API domains: messages, certificates, contact, github, hearts, judging, leaderboard, llm, newsletters, problemstatements, slack, store, teams, users, validate, volunteers.

### Database layer (`db/`)
- `db/interface.py` — Abstract `DatabaseInterface` base class.
- `db/firestore.py` — Production implementation using Firebase Firestore.
- `db/mem.py` — In-memory implementation (enabled via `IN_MEMORY_DATABASE=True` env var).
- `db/db.py` — Singleton that selects the implementation and re-exports all DB operations as module-level functions. All service code imports from `db.db`.

### Models (`model/`)
Data classes (User, Hackathon, Nonprofit, ProblemStatement, JudgeAssignment, JudgeScore, JudgePanel, etc.) with `serialize()`/`deserialize()` methods for Firestore document conversion.

### Auth
PropelAuth via `common/auth.py`. Routes get the authenticated user from `@auth.require_user` and org context from the `X-Org-Id` header.

### Caching
`common/utils/redis_cache.py` — Redis with TTLCache fallback. Used via `@cached_with_key()` decorator.

### Logging
`common/log.py` — Structured JSON logging with `get_logger()`, `info()`, `debug()`, `warning()`, `error()`, `exception()` helpers. Supports colored console output.

### Key environment variables
`FLASK_APP=api`, `FLASK_RUN_PORT=6060`, `CLIENT_ORIGIN_URL`, `FIREBASE_CERT_CONFIG`, `OPENAI_API_KEY`, `PROPEL_AUTH_KEY`, `PROPEL_AUTH_URL`, `REDIS_URL` (optional), `IN_MEMORY_DATABASE` (optional), `ENVIRONMENT=test` (for MockFirestore).

### Profile data sources
- Hackathon attendance is derived from the `volunteers` collection via `services.volunteers_service.get_user_hackathon_attendance(user_id, email)` — hackers always count; mentors/judges/volunteers require `isSelected=True` AND `checkInTime`. The legacy `users.hackathons` Firestore-ref array is no longer the source of truth.
- Public profile (`GET /api/users/<db_id>/profile/public`) augments with `hackathon_history` (volunteer-derived), `praises_count`, and `praises_recent` (top 3) when the corresponding privacy field is `"public"`.
- Privacy fields (`model.user.privacy_fields`) include `praises`, which defaults to `"public"` (others default to `True` which is treated as private).
- New public route: `GET /api/users/<db_id>/praises?limit=&offset=` returns paginated received praises (403 when private).

## Deployment
Deployed to Fly.io (`fly.toml`, app: `backend-ohack`, region: `sjc`). Uses gunicorn (`Procfile`). Port 6060.
⚠️ As of June 2026 prod runs ONE sync gunicorn worker (`Dockerfile` CMD `--workers 1`, default sync class) — a single slow request blocks the entire API. Fix plan (workers/threads, caching, Sentry error sweep, traffic evidence): `docs/perf-reliability-plan-2026-06.md`.

## Testing notes
- Tests live in `api/<domain>/tests/` or `test/` at the repo root.
- The app has heavy external dependencies (Firestore, OpenAI, PropelAuth, Slack, PIL). Tests must pre-mock these modules in `sys.modules` before importing service code.
- `ENVIRONMENT=test` enables MockFirestore in the DB layer.

## Code Style Guidelines
- Python 3.9.13 (Flask backend)
- Imports: Group standard library, then third-party, then local imports
- Types: Use type hints for function parameters and return values
- Naming: snake_case for variables/functions, PascalCase for classes
- Error handling: try/except with specific exceptions
- Linting: pylint (`.pylintrc` disables missing-module-docstring, missing-function-docstring, too-few-public-methods)

## Gotchas (load-bearing — every one of these has bitten us)

### Python 3.9 — no PEP 604 union syntax
Backend runs on Python 3.9. `def foo() -> X | None:` raises `TypeError` at *import time*, blowing up every endpoint that imports the module. Use `Optional[X]` from `typing`. Audit any new `services/` module before committing.

### PropelAuth user object — correct attribute names
`auth_user` (`current_user` from `propelauth_flask`) wraps the full PropelAuth `User` class. The attributes are NOT what they look like:

- `user.org_id_to_org_member_info` — dict of `{org_id: OrgMemberInfo}`. **NOT** `org_id_to_org_info` (which silently returns `None` from `getattr`, leaving every admin check returning `False`).
- Each `OrgMemberInfo` is an *object*, not a dict. Use `.user_permissions` (attribute) or `.user_has_permission(perm)` (method). `org_info.get("user_permissions")` always returns `None`.

Pattern for "is this user a global admin":
```python
def is_admin(propel_user) -> bool:
    if not propel_user or not getattr(propel_user, "user_id", None):
        return False
    for org_info in (getattr(propel_user, "org_id_to_org_member_info", None) or {}).values():
        if org_info.user_has_permission("volunteer.admin"):
            return True
    return False
```

For most route protection, prefer the existing decorator: `@auth.require_org_member_with_permission("volunteer.admin", req_to_org_id=getOrgId)`. Only roll your own check when combining multiple gates (per-resource editors list, etc.).

### Firestore compound queries require composite indexes in production
`.where("X", "==", v).order_by("Y")` (where `Y != X`) needs a composite index declared in `firestore.indexes.json` AND deployed via `firebase deploy --only firestore:indexes`. The local Firestore emulator silently allows these queries; production Firestore returns 500 with "The query requires an index" — the route just hangs/errors.

Two options:
1. **Sort in Python after a single-field where** (preferred when the result set is small): `sorted([... for d in coll.where(...).stream()], key=lambda x: x["pos"])`. No index needed because single-field equality is auto-indexed.
2. **Add the composite index to `firestore.indexes.json`** AND deploy. Don't forget the deploy step — committing to the repo doesn't apply it.

### `set(..., merge=True)` DEEP-merges map fields — removing a nested key needs DELETE_FIELD
`ref.set({"some_map": {...}}, merge=True)` does NOT replace `some_map` — it recursively merges, so a key you dropped from the Python dict stays in Firestore. To delete a nested key you must write `firestore.DELETE_FIELD` at that exact path: `ref.set({"some_map": {key: firestore.DELETE_FIELD}}, merge=True)`. MockFirestore (test/`ENVIRONMENT=test`) REPLACES maps instead of deep-merging, so this passes locally and only breaks in prod. This caused the "mentor coverage cleared but stays checked" bug — `toggle_mentor_coverage` (`api/mentors/mentors_service.py`) popped the slug then wrote the dict, which never removed it. Pattern to copy is there now: write only the changed nested keys, DELETE_FIELD to remove. Mixing DELETE_FIELD sentinels and real values in the same nested map is allowed; DELETE_FIELD on a non-existent path is a no-op.

### Lazy user profile creation in the `users` collection
A user authenticated via PropelAuth may NOT exist in the Firestore `users` collection. The collection is populated lazily — only when someone hits `GET /api/users/profile` or saves profile metadata. Never assume `fetch_users()` includes everyone with a `propel_user_id` referenced elsewhere (assignees, editors, mentions, etc.).

When resolving propel_id → display profile, fall back to `services.users_service.get_oauth_user_from_propel_user_id(pid)` for IDs not in the cached `fetch_users()` index. Cache the fallback aggressively (5 min minimum) — PropelAuth API calls aren't free.

### OAuth provider response shapes (Slack vs Google)
`get_oauth_user_from_propel_user_id(propel_id)` returns the raw OAuth userinfo. Provider-specific fields:

- **Slack**: `https://slack.com/user_id` (e.g. `UC31XTRT5`) is the Slack workspace user ID. `https://slack.com/user_image_192` for avatar. `email` always present.
- **Google**: no Slack ID. `picture` for avatar. `email` always present.
- Detect by presence of `https://slack.com/user_id` — Google responses don't have it.

To send a Slack DM, pass the Slack user ID as `channel`: `send_slack(message=..., channel="UC31XTRT5")`. `chat.postMessage` opens (or reuses) a DM channel.

### `User.id` vs `User.user_id`
- `User.id` = Firestore document ID (used by `/api/users/{id}/profile/public` and the frontend `/profile/{id}` route).
- `User.user_id` = PropelAuth user ID (the `propel_id`, what's stored in `assignees[]`, `editors[]`, etc.).

These are DIFFERENT VALUES. When bundling user data for the frontend, include both: `{user_id: propel_id, db_id: firestore_doc_id, name, profile_image}`. The frontend needs `db_id` to build profile links and `user_id` (propel) for matching against assignees/editors/mentions.

## Admin Email Templates (`email_templates` collection)
Powers the frontend `/admin/communication` template editor and the volunteer send-email dialogs. Blueprint: `api/email_templates/email_templates_views.py` (`/api/admin/templates*`, all `volunteer.admin`-gated); service: `services/email_templates_service.py`; seed data: `services/email_templates_seed.py`.
- Layout: `email_templates/{slug}` main doc + `email_templates/{slug}/versions/{000N}` append-only content snapshots. `version` on the main doc = current version number; version doc ids are zero-padded for natural ordering.
- Versioning rules: content-key edits (`title/category/category_key/applicable_roles/message/icon`) bump version + append a snapshot; status-only patches don't bump; **revert never rewrites history** — it copies the target version's content forward as a NEW version (`change_note: "Reverted to version N"`).
- Seeding: `email_templates_seed.py` holds the original 22 hardcoded frontend templates (GENERATED from frontend `src/lib/messageTemplates.js` — regenerate, don't hand-edit; editing it does not change live emails). Auto-seeds on first list call when the collection is empty; `POST /api/admin/templates/seed` re-inserts missing seed docs only — never overwrites admin edits.
- DELETE is a hard delete (doc + versions). Deleted seed templates can be restored via /seed (history restarts at v1).

## Hackathon roster import + audit scripts
Two scripts live in `scripts/` for diagnosing and backfilling team rosters on `/hack/<event_id>`:

- `audit_hackathon_team_users.py --event-id <id>` (read-only) — walks `hackathons/{id}.teams[] -> teams/{id}.users[]` and reports per-team member counts, dangling refs (team points to deleted user doc), and "ghost" users (no name + no propel_id = imported but never logged in).
- `import_hackathon_users_from_csv.py --csv <path> --event-id <id> --csv-type {registrants|projects|roster} [--apply]` — dry-run by default. `projects` parses Devpost projects CSVs; the team-member triplet offset is resolved by header lookup (`Team Member 1 First Name`), since old 23-col exports have no "Team Number" column while newer 24-col ones do. Each parsed "email" is validated with the email regex — rows where the triplet shifted off-axis are skipped with a warning rather than written as bogus user docs. `roster` parses a generic `team,email[,first_name,last_name,name]` CSV for backfilling memberships; `registrants` just seeds user docs. Users are matched by `email_address` (case-insensitive). Imported users get `imported=True`, `import_source`, `import_event_id`, blank `user_id`/`propel_id`. Team membership writes are additive — never removes existing members. Re-runnable.
- `cleanup_bogus_imported_users.py [--event-id <id>] [--apply]` — finds and removes the user docs left behind by the older off-by-one `parse_projects` bug. Fingerprint: `imported=True` AND `propel_id=""` AND `email_address` present but not a valid email AND `import_source` starts with `projects-`. For each matched user it prunes the doc-ref from every team's `users[]` that references it, then deletes the user doc. Dry-run by default. After running, re-run `import_hackathon_users_from_csv.py --csv-type projects` against the affected events to import the real members.
- `backfill_devpost_winners.py --event-id <id> --devpost-url <url> [--projects-csv <path>] [--apply]` — scrapes the Devpost project gallery for EVERY project tile, flagging winners (`aside.entry-badge img.winner`). For each project it matches to a Firestore team via a layered strategy: `teams.devpost_link` exact-URL → team name (case-insensitive) → email-overlap via Devpost projects CSV (auto-discovered from `/tmp/devpost_files/<event_id>/projects-*.csv`). Two backfills happen in one pass: (1) any matched team with an empty `devpost_link` gets the gallery URL written; (2) matched WINNERS additionally get `/software/<slug>` fetched for prize text + member names, with prize strings mapped to status — "1st place" → `FOUNDING_ENGINEERS`, "Completion" or "2nd place" → `COMPLETION_SUPPORT`, anything else marked Winner → `CATEGORY_WINNER` (rank-based; multi-prize teams get the best status, all prize text retained in `awards: []`). Conflicts (team already has a different `devpost_link`) are logged but never overwritten. Unmatched winners exit with code 2 so a human notices; unmatched non-winners are listed for visibility but don't fail the run (typical for teams that registered only on Devpost). Only sets `status`, `awards`, `winners_backfilled_at/source`, and `devpost_link`; never touches `users[]`. Re-runnable. Adds `beautifulsoup4` to requirements.

## Resend API constraints
`GET /emails` (list) supports ONLY `limit` (max 100)/`after`/`before` — no recipient or date filter; results newest-first. Per-recipient status = `Emails.get(id)` using the `resend_id` stored in volunteer `sent_emails`, or webhooks. Rate limit is low single-digit req/s per team and shared with email sending.

### Resend status architecture (implemented 2026-06-10)
- **Primary path**: `GET /api/admin/emails/resend-status` — per-ID `Emails.get` with Redis cache (`resend:status:{id}`). Terminal events (`delivered`, `bounced`, `complained`, etc.) TTL 7 days; transient (`sent`, `queued`, etc.) TTL 120 s. Cap 100 IDs, 0.3 s throttle between calls.
- **List crawl**: `POST /api/admin/emails/resend-list` — never blocks on page load. Always returns from cache immediately. Pass `force: true` to kick a background crawl. Date-bounded to 90 days; max 30 pages; 0.5 s between pages. Cache TTL 15 min fresh / 1 hour stale.
- **Admin UI**: on volunteer load, bulk-fetches IDs from `sent_emails` via the status endpoint. "Sync from Resend" button sends `force: true` and shows a 30 s snackbar if the sync is still running.
- **Confirmation email tracking**: `send_volunteer_confirmation_email` now accepts `volunteer_id` and appends a `sent_emails` record with `recipient_type: 'application_confirmation'` after sending.

## Resend audience sync
`scripts/sync_resend_audience.py --source {all|profiles|volunteers|mentors|judges|sponsors|helpers|leads} --audience "<name>" [--event-id <id>] [--selected-only] [--apply]` — pulls emails from Firestore (`users.email_address`, `volunteers.email` filtered by `volunteer_type`, `leads.email`) and upserts contacts into a Resend audience (creates if missing). Dry-run by default. Re-runnable: lists existing audience contacts first and only POSTs new emails. Needs `RESEND_API_KEY` with audiences scope — the existing `RESEND_WELCOME_EMAIL_KEY` is send-only and will 401. Uses the deprecated `resend.Audiences` SDK class (now an alias for Segments) — fine for now, but if it breaks switch to `resend.Segments`.

The frontend `/hack/<event_id>` page's "Team Members:" list is `teams.users[]` (DocumentReferences). The bug pattern that motivated this: a team's `users[]` only contains the user who created the team on ohack.dev; everyone else registered via Devpost/JotForm and was never linked. Use `audit` first to confirm, then `import ... --csv-type roster` (or `projects` for old Devpost exports) to backfill.
