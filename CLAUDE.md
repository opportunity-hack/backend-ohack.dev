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

## Hackathon roster import + audit scripts
Two scripts live in `scripts/` for diagnosing and backfilling team rosters on `/hack/<event_id>`:

- `audit_hackathon_team_users.py --event-id <id>` (read-only) — walks `hackathons/{id}.teams[] -> teams/{id}.users[]` and reports per-team member counts, dangling refs (team points to deleted user doc), and "ghost" users (no name + no propel_id = imported but never logged in).
- `import_hackathon_users_from_csv.py --csv <path> --event-id <id> --csv-type {registrants|projects|roster} [--apply]` — dry-run by default. `projects` parses Devpost projects CSVs (variable-length team-member triplets starting at col 22, 1-indexed); `roster` parses a generic `team,email[,first_name,last_name,name]` CSV for backfilling memberships; `registrants` just seeds user docs. Users are matched by `email_address` (case-insensitive). Imported users get `imported=True`, `import_source`, `import_event_id`, blank `user_id`/`propel_id`. Team membership writes are additive — never removes existing members. Re-runnable.

## Resend audience sync
`scripts/sync_resend_audience.py --source {all|profiles|volunteers|mentors|judges|sponsors|helpers|leads} --audience "<name>" [--event-id <id>] [--selected-only] [--apply]` — pulls emails from Firestore (`users.email_address`, `volunteers.email` filtered by `volunteer_type`, `leads.email`) and upserts contacts into a Resend audience (creates if missing). Dry-run by default. Re-runnable: lists existing audience contacts first and only POSTs new emails. Needs `RESEND_API_KEY` with audiences scope — the existing `RESEND_WELCOME_EMAIL_KEY` is send-only and will 401. Uses the deprecated `resend.Audiences` SDK class (now an alias for Segments) — fine for now, but if it breaks switch to `resend.Segments`.

The frontend `/hack/<event_id>` page's "Team Members:" list is `teams.users[]` (DocumentReferences). The bug pattern that motivated this: a team's `users[]` only contains the user who created the team on ohack.dev; everyone else registered via Devpost/JotForm and was never linked. Use `audit` first to confirm, then `import ... --csv-type roster` (or `projects` for old Devpost exports) to backfill.
