# Backend Performance & Reliability Plan — June 2026

**Executor:** Sonnet 4.6 (Claude Code). Work through phases in order. Each task lists files, the change, and acceptance criteria.
**Repos:** `backend-ohack.dev` (primary) and `frontend-ohack.dev` (Phase 5 tracking fixes).

## Execution guardrails

- Line numbers below were verified June 2026 but may drift — always `grep` for the quoted code before editing.
- **`api/messages/messages_views.py` and `api/messages/messages_service.py` are frozen for NEW code** (backend CLAUDE.md). In-place bug fixes to existing functions are allowed; anything new goes in a per-domain blueprint/service. If a fix grows beyond a guard clause, migrate the function out instead.
- Logging: use the structured helpers in `common/log.py` (`error()`, `warning()`, `info()`). The Sentry-spam fixes below are mostly "downgrade expected conditions from error → warning/info".
- Tests: `pytest` from repo root (conda env `py39_ohack_backend`). Heavy external deps are pre-mocked in `sys.modules` — follow existing test patterns.
- Caching: `common/utils/redis_cache.py` (Redis with TTLCache fallback, `@cached_with_key()`) already exists and is used by `api/slack/slack_service.py` and `services/volunteers_service.py`. Prefer it for expensive/shared results. Plain `cachetools.TTLCache` is fine for per-process short-TTL caches, but see P0-1 thread-safety note.
- Do not cache OAuth **tokens** in Redis. PropelAuth responses containing access tokens stay in in-process memory only (short TTL).

## Evidence (what drove these priorities)

**Sentry span export, prod, 2026-05-29 → 2026-06-12 (n=12,835):**

| transaction | calls | p50 | p95 | total time |
|---|---|---|---|---|
| `get_hackathon_funnel_aggregate_api` | 119 | **7.7s** | 12.4s | 661s |
| `get_single_hackathon_by_event` | 871 | 2.6ms | **4.6s** | 531s |
| `api-messages.profile` | 960 | 3.1ms | **2.1s** | 416s |
| `api-leaderboard.get_leaderboard_by_event_id` | 548 | **727ms** | 1.5s | 339s |
| `get_npos_by_hackathon_id_api` | 558 | 171ms | 529ms | 119s |
| `save_profile` | 145 | 781ms | 1.0s | 113s |
| volunteer-by-event ×4 (mentor/hacker/judge/volunteer) | ~2,848 | 63–109ms | ~300ms | 271s |
| `planning.get_board` | 26 | 2.4s | 2.9s | 59s |
| `volunteers.admin_list_resend_emails` | 4 | **~20s** | 20s | 40s |
| `get_praises` | 45 | 1.9ms | 3.6s | 44s |
| `get_privacy_settings` | 113 | 328ms | 607ms | 40s |
| `get_my_volunteer_status_for_event` | 39 | 611ms | 1.4s | 24s |
| `get_judge_application` / `get_hacker_application` | 89 / 73 | ~500ms | ~900ms | 77s |
| `get_single_problem` / `list_hackathons` | 1,333 / 1,256 | ~2ms | — | healthy |

**GA (Mar 13 – Jun 10):** top public pages are `/hack` (4.6k views — calls the 7.7s funnel aggregate via `HackathonStoryStrip`), home (3.4k), onboarding (3.4k), event pages, `/profile` (1.3k). Two tracking anomalies: `(not set)` page title is the #2 row (9,675 views / 4,092 users), and "Team Management - Opportunity Hack Admin" logged **22,647 views from 3 users** (shallow-route page_view spam — Phase 5).

**Top Sentry error issues:** PropelAuth OAuth lookup failures (734), leaderboard "org/achievements not found" (275+275), `get_giveaway` TypeError (133), leaderboard "Hackathon not found: summer-2025" (57), plus ~10 smaller unhandled TypeErrors detailed below.

---

## Phase 0 — Deployment & concurrency (highest leverage, smallest diff)

### P0-1: Fix gunicorn — prod serves ONE request at a time

`Dockerfile` CMD is:

```
CMD ["venv/bin/gunicorn", "api.wsgi:app", "--log-file=-", "--log-level", "debug", "--preload", "--workers", "1", "--timeout", "120"]
```

One **sync** worker on a 1-CPU/1GB Fly VM = a single 7.7s funnel-aggregate request (or a 20s resend-emails request) head-of-line-blocks every other user. This explains the huge p95s on otherwise-cached endpoints (e.g. `get_single_hackathon_by_event` p50 2.6ms / p95 4.6s). The workload is almost entirely I/O-bound (Firestore, PropelAuth, Slack, GitHub).

**Change (Dockerfile + Procfile, keep both in sync):**

```
--worker-class gthread --workers 2 --threads 8 --timeout 120 --log-level info
```

- Keep `--preload`. Remove `debug` log level in prod (it's noisy and slow).
- Also fix the latent `git-fame` PATH bug while in the Dockerfile (see P2-6): add `ENV PATH="/app/venv/bin:$PATH"`.
- **Thread-safety follow-up (required):** with `gthread`, the module-level `cachetools.TTLCache`s are hit concurrently. `@cached(...)` without a lock can corrupt the underlying dict under threads. Sweep all `@cached(cache=TTLCache(...))` usages (`grep -rn "TTLCache" services/ api/ common/`) and add `lock=threading.Lock()` to each `@cached` decorator (cachetools supports `@cached(cache=..., lock=...)`). This is mechanical; do it in the same PR.
- Memory check: 2 preloaded workers of this app fit in 1GB; if Fly OOMs, drop to `--workers 1 --threads 16` (still fixes head-of-line blocking).

**Acceptance:** deploy boots; `fly logs` shows gthread workers; p95 of `get_single_hackathon_by_event` drops to <500ms within a day.

### P0-2: Crash-bug sweep — unhandled TypeErrors returning 500s

Most share one root cause: `get_slack_user_from_propel_user_id()` / `get_oauth_user_from_propel_user_id()` (in `services/users_service.py`) can return `None`, and callers immediately subscript it.

1. **Add a shared resolver helper** (new code → put in `services/users_service.py` or a small new module, NOT messages_service):
   ```python
   def resolve_db_user_or_none(propel_user_id):
       """propel UUID → (slack_user_dict, db_user) or (None, None); never raises."""
   ```
   Then guard each caller and return a proper 4xx JSON response instead of crashing:
   - `services/feedback_service.py` ~line 32 — `slack_user["sub"]` after `get_slack_user_from_propel_user_id` (Sentry: `get_feedback` TypeError, 3 events).
   - `services/giveaway_service.py` ~lines 20–21, 46–47 — same pattern (Sentry: `get_giveaway` TypeError, **133 events**). Also `get_all_giveaways` ~line 70: `get_user_by_id_old` result can be None before `giveaway["user"] = user` consumers subscript it.
   - `api/messages/messages_service.py` ~lines 83–84 (`register_helping_status`) — same pattern (11 events). In-place guard only (frozen file).
2. **`api/users/users_views.py` `save_volunteering_time`** (~lines 59–67): returns `None` → Flask "did not return a valid response" (21 events). Return `({"error": "..."}, 400/404)` on every path. Also fix the three implicit `return` (None) paths in `services/users_service.py::save_volunteering_time` (~391, 400, 418) to return explicit errors the view can map.
3. **`get_volunteering_time`** — view at `api/users/users_views.py` ~line 77 unpacks a 3-tuple; service (~line 437/446) returns `None` on missing oauth user / missing db user. Make the service raise a typed error or return `(None, None, None)` and have the view return 404 (6 events).
4. **`services/users_service.py::get_profile_metadata`** ~line 317: `save_user(...)` returns `None` when PropelAuth gave empty values → `db_id.id` AttributeError (10 events, plus 10 paired "Empty values provided for user save"). Guard `db_id is None` → return 4xx; downgrade the "Empty values" log to warning.
5. **`api/messages/messages_service.py::get_single_problem_statement*`** ~line 261: `doc_to_json` can return None → `result["id"] = doc.id` TypeError (2 events). Guard, return `{}`/404.
6. **`services/hackathons_service.py::update_*_by_event_id`** (~line 717): Sentry KeyError `"'slack_user_id' is not contained in the data"`. Firestore `DocumentSnapshot.get(field)` **raises KeyError** when the field is absent — use `doc.to_dict().get('slack_user_id')` instead, and if it's falsy, skip all Slack messaging for that volunteer (log warning) but still complete the update and return the success Message (6 events across mentor/hacker variants).

**Acceptance:** each listed Sentry issue's code path has an explicit guard + non-500 response; add/extend unit tests for the None paths where a test scaffold already exists.

---

## Phase 1 — Hot-path performance

### P1-1: `get_hackathon_funnel_aggregate` — 7.7s p50, fired by `/hack` (top page)

`services/hackathons_service.py` ~339–502, route in messages_views (`/api/messages/hackathons/funnel/aggregate`).

Problems: streams **every** hackathon doc, reads each one's `funnel/summary` subdoc sequentially, and runs a **per-hackathon** `volunteers.where(event_id==X).where(volunteer_type=="hacker")` query. `@cached(TTLCache(maxsize=1, ttl=600))` means a full 7.7–14s recompute every 10 minutes — and on the current single sync worker, every recompute blocks the whole site.

**Changes:**
1. Replace the per-hackathon volunteers query with **one** query: `db.collection("volunteers").where("volunteer_type", "==", "hacker").stream()` selecting only `event_id` (use `.select(["event_id"])`), then `Counter` by event_id in memory. (If volunteer count is huge, use Firestore **count aggregation queries** per event in parallel instead — but single-scan-and-count is simpler and fine at OHack scale.)
2. Batch the funnel `summary` subdoc reads: build all `DocumentReference`s first, then one `db.get_all(refs)`.
3. Cache the final aggregate in **Redis** via the existing `cached_with_key()` helper, TTL **6 hours** (the data is historical; it only moves during an event). Keep the in-process TTLCache as fallback. On cache-compute, log duration.
4. Frontend already tolerates slow/skeleton states (`HackathonStoryStrip` reserves height) — no frontend change needed.

**Acceptance:** endpoint p50 < 300ms warm, recompute < 2s; `/hack` no longer shows multi-second funnel skeletons.

### P1-2: Leaderboard — 548 calls × 727ms + 607 error events / 2 weeks

`api/leaderboard/leaderboard_service.py` (+ `leaderboard_views.py`).

1. **Hackathon fetched 5× per request** (`get_hackathon_by_event_id` at ~25, ~115, ~164, ~624, and ~526 inside `collect_mentor_panel_opportunities`). Fetch once in `get_github_leaderboard(event_id)`, pass the doc down as a parameter.
2. **Cache the whole response**: `@cached_with_key("leaderboard", ttl=300)` on `get_github_leaderboard`. GitHub-derived stats don't change minute-to-minute.
3. **Fix broken existence checks:** ~line 180 `if not organization_ref:` (a DocumentReference is always truthy) and ~line 189 `if not organization_ref.collection('achievements').get()` (dead check). Use `.get().exists` / stream-and-check.
4. **Log-level hygiene:** "Hackathon not found for event ID" (~27, ~117, ~167), "Organization … not found in database" (~73, ~181), "No achievements found" (~190) are expected conditions handled gracefully — downgrade `error` → `info`/`warning`. This kills 607 Sentry events/2wk.
5. **`collect_mentor_panel_opportunities`** (~508–604) runs a full teams scan on every leaderboard request. Cache it for 5 min, and short-circuit (return `[]`) when `now` is outside the event live window (`start_date <= now <= end_date + 1d`) **before** any Firestore reads.
6. **Data fixes (one-time, do with Greg's confirmation):**
   - Some hackathon doc has `github_org: "safespace-app-for-teens"` but no matching doc in `github_organizations` (275+275 errors). Either create the org doc, clear the field, or fix the slug.
   - Something requests event id `summer-2025` (57 errors) which doesn't exist (canonical is likely `2025_summer`). Grep the frontend for the caller of `/api/leaderboard/summer-2025`; apply the same `season-YYYY → YYYY_season` alias normalization the frontend uses for event pages, or normalize the alias server-side in `get_hackathon_by_event_id`.

**Acceptance:** leaderboard p50 < 100ms warm; the three Sentry issues stop accruing.

### P1-3: Cache PropelAuth OAuth lookups (affects ~6 endpoints + login)

`services/users_service.py::get_oauth_user_from_propel_user_id` (~172–222) makes a blocking HTTP call to PropelAuth on **every** call, sometimes followed by a second HTTP call to Slack/Google token endpoints. Callers: profile save/load, volunteering time, privacy settings, mentor/judge status (`_resolve_caller` in `api/mentors/mentors_service.py`), application gets. This is why `get_privacy_settings` is 328ms p50, `get_my_volunteer_status_for_event` 611ms, judge/hacker application gets ~500ms, and `api-messages.profile` p95 2.1s.

**Changes:**
1. Add `@cached(cache=TTLCache(maxsize=512, ttl=600), lock=threading.Lock())` to `get_oauth_user_from_propel_user_id` (in-process ONLY — the payload contains access tokens; do not put it in Redis).
2. **Negative caching:** when PropelAuth returns 404 for a propel_id, cache the `None` result too (a smaller TTLCache, ttl=300) so a deleted/broken user with an active session (the source of the **734** "Could not get OAuth user from PropelAuth" errors — note Sentry shows one propel_id `ef56954d-…` dominating) doesn't hammer PropelAuth on every request. Have `get_privacy_settings` return 404 (not None→500-ish) so the frontend stops retrying.
3. Downgrade the "Could not get OAuth user" logs at the 5 call sites (~337, 380, 436, 542, 561) from `error` → `warning` — it's an expected client-state condition now handled with a 404.

**Acceptance:** `get_privacy_settings` p50 < 50ms warm; `get_my_volunteer_status_for_event` p50 < 200ms; PropelAuth error volume drops to near zero.

### P1-4: `api-messages.profile` (login path, 960 calls, p95 2.1s)

`api/messages/messages_service.py::get_profile_metadata_old` (~299–335). It does PropelAuth HTTP → `save_user_old` Firestore write → history read, per login. P1-3's cache removes the PropelAuth call. Additionally (in-place edits only — frozen file):
- Skip the `save_user_old` write when nothing changed except `last_login`, unless last_login is > 1 hour stale (read-compare-write you're already doing the read for). This halves write load on the hottest auth path.

**Acceptance:** profile p95 < 800ms.

---

## Phase 2 — Secondary wins & hygiene

### P2-1: `get_npos_by_hackathon_id` N+1 (558 calls, 171ms p50)
`services/nonprofits_service.py` ~154–162: per-ref `.get()` loop → replace with one `db.get_all(npo_refs)`. Keep `doc_to_json` conversion. **Acceptance:** p50 < 80ms.

### P2-2: Slack utils hardening (`common/utils/slack.py`)
1. `get_slack_user_by_email` (~130–143): add the same decorator stack its sibling has (`@cached(TTLCache(maxsize=500, ttl=86400), lock=...)`, `@sleep_and_retry`, `@limits(calls=20, period=60)`); `users.lookupByEmail` is tightly rate-limited (Sentry: 28 failures). Cache None results too (ttl ~3600) so unknown emails don't refetch. Downgrade the failure log to warning — application submission already proceeds without slack_user_id.
2. `send_slack` (~307–340): replace `assert e.response["error"]` in the except branch with a `warning()` log; never let a Slack failure raise out of business logic. Audit callers in `register_helping_status` (messages_service ~160–182): wrap channel sends in try/except so `is_archived` / `channel_not_found` (Sentry, 7 events) don't fail the user's request — log warning and continue.
3. `is_channel_id` (~192–200): downgrade the `conversations.info` failure log to `debug` — falling back to name lookup is the designed path (Sentry: "Error checking channel ID 2026_summer_wial").
4. **invalid_blocks** (Sentry, `update_mentor_application`): in `services/volunteers_service.py::send_slack_volunteer_notification` (~381–507), Slack caps a section `text` object at **3000 chars**. Truncate each detail line (esp. `availableDays`, free-text fields) to ~500 chars and the joined section to 2900 with an ellipsis; if over, split into multiple section blocks (max 50 blocks).

### P2-3: `planning.get_board` (2.4s p50, low volume)
`api/planning/planning_views.py` ~151–200: three sequential subcollection streams + profile resolution. Low call count — keep it cheap: run the three streams concurrently via `concurrent.futures.ThreadPoolExecutor` (safe under gthread), and confirm `_resolve_public_user_profiles` batches user reads with `db.get_all` (fix if it loops `.get()`). **Acceptance:** p50 < 1.2s.

### P2-4: `admin_list_resend_emails` ~20s spans
`services/volunteers_service.py` ~2890–2976. Design intent is background refresh + immediate cached return, but 20s server spans show the fetch sometimes runs inline (cold Redis / lock miss). Verify the cold path returns `{emails: [], syncing: true}` immediately and **always** delegates the 30-page Resend crawl to the background thread; add a module-level "refresh in flight" guard so concurrent admins can't trigger parallel crawls. **Acceptance:** no request span > 2s on this endpoint.

### P2-5: `get_praises` p95 3.6s
Same per-process cold-cache pattern (`services/news_service.py` area, `@cached` ttl 600). After P0-1 the blocking-induced tail should mostly disappear; if a cold compute is still >2s, batch its Firestore reads the same way as P2-1. Verify, don't over-engineer.

### P2-6: Fix `git-fame` FileNotFoundError (certificates)
`api/certificates/scan_repo.py` ~118 runs `subprocess.run(["git-fame", ...])`. `git-fame==2.0.1` IS in requirements.txt and pip-installs its CLI into `/app/venv/bin/`, but the Dockerfile never puts the venv on PATH (CMD calls `venv/bin/gunicorn` by explicit path). Add `ENV PATH="/app/venv/bin:$PATH"` to the Dockerfile (done in P0-1) **or** invoke as `[sys.executable, "-m", "gitfame", ...]`. Prefer the PATH fix + a startup log if `shutil.which("git-fame")` is None.

### P2-7: Sentry log-level sweep
After the targeted fixes above, do one pass: `grep -rn "error(logger" services/ api/ common/ | grep -iv except` and downgrade any "expected absence" conditions (not-found lookups that return gracefully) to warning/info. Goal: Sentry error feed = actionable issues only.

---

## Phase 3 — Frontend ↔ backend traffic shape (optional, propose-only)

The event page (`/hack/[event_id]`) fans out to ~8 backend calls (4 separate volunteer-type fetches, leaderboard, funnel, npos, teams, github). With Phase 0–1 done this is tolerable. **Do not build now** — leave a note in the plan PR description proposing a consolidated `GET /api/hackathon/<id>/page-bundle` for a future cycle.

---

## Phase 4 — Verification (backend)

1. `pytest` green; `pylint api/ common/ db/ model/ services/` no new errors.
2. Deploy to staging (fly `backend-ohack-staging` exists per Sentry), smoke: `/api/messages/hackathons/funnel/aggregate` (<2s cold), leaderboard for a real + a bogus event id (200 empty, no error logs), `/api/users/volunteering` GET/POST with a bad token (4xx not 500).
3. After prod deploy, watch Sentry for 48h: the 15 listed issues should stop accruing; span p95s should compress per acceptance criteria. Mark each Sentry issue Resolved so regressions re-alert.

---

## Phase 5 — Frontend event-tracking fixes (`frontend-ohack.dev`)

The GA export shows three measurement defects that corrupt the traffic data this plan is based on — fix them so the next optimization cycle has clean numbers.

### P5-1: Double page_view per call in `src/lib/ga/index.js::pageview()`
It fires `gtag('config', GA_ID, pageParams)` (which sends a page_view by default) **and** `gtag('event', 'page_view', pageParams)`. Keep only the `event` call; never re-issue `config` per page (it also resets config state).

### P5-2: SPA pageview wiring + shallow-route spam
There is **no** `routeChangeComplete` subscription in `_app.js`; SPA pageviews come from GA4 Enhanced Measurement history tracking, which fires on **every** `pushState`/`replaceState` — including the app's many shallow `router.replace` query-param writes (admin `?section=`/`?subtab=`, the 250ms-debounced `?q=` search sync, dialog-state params). Result: "Team Management" admin logged 22,647 views from 3 users, drowning real traffic.

1. In `_app.js`, subscribe once:
   ```js
   useEffect(() => {
     const onDone = (url, { shallow }) => { if (!shallow) ga.pageview(url); };
     router.events.on("routeChangeComplete", onDone);
     return () => router.events.off("routeChangeComplete", onDone);
   }, [router.events]);
   ```
   Fire `pageview` inside a `setTimeout(0)`/microtask so `next/head` has applied the new `document.title` (this also fixes most of the 9,675 `(not set)` titles).
2. **Manual step for Greg (cannot be done in code):** in GA4 Admin → Data Streams → Enhanced Measurement, turn OFF "Page changes based on browser history events". Without this, step 1 double-counts. Call this out in the PR description.
3. Initial load stays tracked by the `gtag('config', …)` in `_document.js` — leave it, but add `send_page_view: true` explicitly and the `page_title` param.

### P5-3: Title coverage + admin segmentation
- `(not set)` (9.7k views) and the bare `| Opportunity Hack` (76 views) rows mean some routes render without a `<title>`. Sweep `src/pages/admin/**` and any page not setting `<Head><title>` (grep for pages lacking `title`), add stable titles.
- Set a GA user property on admin routes (`traffic_segment: 'admin'` via `gtag('set', 'user_properties', …)` when `pathname.startsWith('/admin')`) so admin usage can be excluded from acquisition reporting.

### P5-4: Key-event gaps worth closing (small)
- The judge funnel is the largest organic cluster (~3k views across judge pages) but `hackathon-judge-opportunities` rows show near-zero key events. Ensure judge/mentor/hacker application forms fire `trackEvent` for form start AND submit (check `src/components/ApplicationForm/*` and the four `*-application.js` pages; add `EventCategory.FORM` start/complete events where missing).
- `Application error: a client-side exception has occurred` appeared as a page title (42 users). Add a GA `exception` event (and consider Sentry browser SDK if absent) in the Next.js error boundary / `_error.js` so client crashes are measurable.

### P5-5: Verification (frontend)
`npm run build` green; with GA DebugView, click around: one page_view per real navigation, zero on admin `?section=` switches and search keystrokes, titles populated.

---

## Suggested PR slicing

1. PR-1 (backend): P0-1 Dockerfile/Procfile + TTLCache locks + P2-6 PATH fix.
2. PR-2 (backend): P0-2 crash-bug sweep (pure guards, low risk).
3. PR-3 (backend): P1-1 funnel aggregate + P1-2 leaderboard.
4. PR-4 (backend): P1-3/P1-4 PropelAuth caching + profile path; P2-1…P2-5, P2-7.
5. PR-5 (frontend): Phase 5 tracking fixes.
6. Data fixes (P1-2 item 6) — separate, after Greg confirms the intended `github_org` / event-id values.
