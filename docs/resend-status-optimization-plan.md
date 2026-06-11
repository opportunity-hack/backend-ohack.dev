# Plan: Stop full Resend email sync on volunteer admin page load

## Problem
`services/volunteers_service.py:_fetch_and_cache_resend_emails()` crawls the ENTIRE Resend
account (up to 100 pages × 100 emails) whenever the 5-min freshness flag expires and an admin
loads `/admin/volunteers`. The frontend (`frontend-ohack.dev/src/components/admin/VolunteerTable.js`)
eagerly POSTs all volunteer emails to `/api/admin/emails/resend-list` on mount, but the backend
ignores that filter for fetching — it's applied AFTER crawling everything. First uncached request
blocks for the whole crawl. Rate limit is shared with actual email sending.

## Hard API constraint (verified June 2026, resend SDK 2.22.0)
Resend's List Emails API (`GET /emails`) supports ONLY `limit` (max 100, default 10), `after`,
`before` cursors. **There is no recipient/`to` filter, no date filter.** Results are newest-first.
So "fetch only emails for users on the page" is impossible via the list endpoint. The per-recipient
primitives available are:
1. `GET /emails/{id}` (`resend.Emails.get`) — works because we already store `resend_id` per send.
2. Webhooks (`email.delivered/bounced/complained/...`, svix-signed) — push, no polling.
3. List crawl — only way to discover emails whose ids we never recorded.

Rate limit: low single-digit req/s per team (docs say default ~2–5 rps, shared across keys and
with email sending). 429 on exceed. Throttle accordingly.

## Existing data we already have (use it)
- Volunteer docs have `sent_emails: [{resend_id, subject, timestamp, sent_by, recipient_type}]`
  for all admin-composed sends (volunteers_service.py ~2394–2428, ~2545–2601). The frontend
  already receives these in the volunteers payload (`getSentEmails` in VolunteerTable.js).
- The bulk list adds only: (a) live `last_event` for stored ids, (b) emails sent by paths that
  don't record ids (e.g. `send_confirmation_email` ~line 304, certificates, contact).
- `last_event` values delivered/bounced/complained/failed/canceled are TERMINAL — cache forever.
  `sent`/`delivery_delayed`/`queued` are transient — short TTL.

## Confirmed design (decided 2026-06-10)
1. DB-first: render each volunteer's email list from Firestore `sent_emails` (already in the
   volunteers payload) — zero Resend calls.
2. Hydrate delivery status (`last_event`) via per-ID `Emails.get` behind the existing
   `/api/admin/emails/resend-status` endpoint, Redis-cached per id.
3. Full Resend list crawl becomes a MANUAL sync (admin button), backend-cached (Redis SWR),
   background-only, date-bounded. It exists only to surface emails with no stored resend_id
   (legacy `messages_sent`, application confirmation emails).

## Phase 1 — make per-ID status the primary path; stop eager full sync

### Backend: `services/volunteers_service.py`
1. Rework `get_resend_email_statuses(email_ids)`:
   - Per-id Redis cache (`common/utils/redis_cache.py` get_cached/set_cached), key
     `resend:status:{id}`. Terminal `last_event` → TTL 7 days; non-terminal → TTL 120 s.
   - Only call `resend.Emails.get` for cache misses; `time.sleep(0.3)` between API calls;
     on a 429/exception, retry once with backoff then mark that id `last_event: 'unknown'`
     WITHOUT caching the failure. Keep the request cap (raise to 100 ids; the frontend chunks).
2. Rework `list_all_resend_emails` (keep route + SWR cache keys):
   - NEVER fetch inline. If cache empty → set lock, start the existing background thread,
     return `{'success': True, 'emails_by_recipient': {}, 'total_fetched': 0, 'syncing': True}`.
   - Accept `force: true` in the POST body (manual sync button): if no refresh lock is held,
     start a background refresh regardless of the fresh flag; always respond immediately with
     current cached data + `syncing` flag. Cache stays shared across all admins.
   - Date-bound the crawl in `_fetch_and_cache_resend_emails`: stop paginating when the last
     item of a page has `created_at` older than a cutoff (param `since_days`, default 90).
     Results are newest-first so this is safe. Lower `max_pages` 100 → 30. Add
     `time.sleep(0.5)` between pages.
   - Bump `_RESEND_FRESH_TTL` 300 → 900 (it becomes on-demand, not per-page-load).

### Frontend: `frontend-ohack.dev/src/components/admin/VolunteerTable.js`
3. Remove the eager `useEffect` that calls `fetchResendEmailList()` when volunteers load
   (~line 352). Keep the function.
4. New effect: when volunteers load, collect unique `resend_id`s from `getSentEmails(v)` across
   all rows (table is unpaginated — all rows render), POST to existing
   `/api/admin/emails/resend-status` in chunks of ≤100 via the existing `fetchResendStatuses`
   (it already dedupes against state). Delivery chips then render from `resendStatuses` — this
   covers every tracked email with mostly Redis cache hits.
5. Keep `fetchResendEmailList` behind an explicit small "Sync from Resend" refresh button
   (e.g. next to the checked-in filter), sending `force: true`, for discovering untracked
   emails (confirmation emails etc.). If response has `syncing: true`, show a snackbar
   "Sync started — refreshing in ~30s" and re-fetch once (without force) after ~30 s.
   Keep tooltip `onOpen` per-id fetch as-is (now cheap due to caching).

### Optional later upgrade (skip for now)
When `resend-status` sees a terminal event, write `last_event`/`last_event_at` back into the
matching volunteer `sent_emails` entry in Firestore so status ships with the volunteers payload
and that id never hits Resend again. Needs an id→volunteer mapping in the request (Firestore
can't query inside arrays of maps by one field) and adds writes — Redis caching already captures
most of the win, so defer.

## Phase 2 — record resend_id at send time (closes most of the gap)
- `send_confirmation_email` (volunteers_service.py ~304): `resend.Emails.send()` returns
  `{'id': ...}`; capture it and append a `sent_emails` record
  (`recipient_type: 'application_confirmation'`) to the volunteer doc right after creation/update.
  Mirror the existing tracking pattern at ~2394. Then per-ID status covers ~everything shown on
  this page and the list crawl becomes a rarely-used backfill.

## Phase 3 (optional end state) — Resend webhooks, zero polling
- New blueprint per CLAUDE.md pattern: `api/resend_webhooks/resend_webhooks_views.py` +
  service. `POST /api/resend/webhook`, svix signature verification (headers svix-id,
  svix-timestamp, svix-signature; secret env `RESEND_WEBHOOK_SECRET`; add `svix` to
  requirements.txt or implement the HMAC-SHA256 check manually — payload is
  `{msg_id}.{timestamp}.{body}` signed with the base64 secret).
- On `email.*` events: write `resend:status:{id}` to Redis (terminal TTL 7d) and optionally
  update the matching volunteer `sent_emails` entry. Read path then never calls Resend.
- Requires registering the endpoint in the Resend dashboard (manual step — note in PR).

## Tests + verification
- Tests in `api/volunteers/tests/` or `test/`; pre-mock `resend`, `db.*`, `common.utils.*` in
  `sys.modules` per CLAUDE.md testing notes. Cover: terminal-vs-transient TTL choice,
  cache-hit skips API call, date-bounded pagination stop, `syncing: True` response when cold.
- Python 3.9: use `Optional[X]`, never `X | None`.
- Boot check: `source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh && conda
  activate py39_ohack_backend && python -c "import api"` (or `flask run` smoke) before claiming done.
- Frontend: `npm run build` in `../frontend-ohack.dev`.
