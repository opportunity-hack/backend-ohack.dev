# `api/giveaways` — giveaways v2 (Nov 2026)

Campaigns with prizes, one weighted entry per person per giveaway with
per-prize opt-in, and a live draw whose results are recorded server-side.
Frontend: `/profile#giveaways` (participants) and `/admin/giveaways`
(setup / entries / draw). Plan: frontend `docs/plans/giveaways-overhaul-2026-11.md`.

The legacy `giveaways` collection and `GET|POST /api/messages/giveaway`,
`GET /api/messages/giveaway/admin` (frozen `messages_views`) are untouched for
old clients. Nothing here reads or writes them, and no data is migrated.

## Collections

- `giveaway_campaigns/{giveaway_id}` — doc id = slug (`fall-2026`), immutable.
  `title, description, event_id, status (draft|open|closed|drawn), must_be_present,
  closes_at (ISO with offset), prizes[{id, name, description, image_url,
  product_url, quantity 1..50, sort_order}], draw{seed, started_at, started_by,
  results[{id, prize_id, slot, user_id, name, profile_image, outcome won|absent,
  attempt, at, by}]}`, timestamps.
- `giveaway_entries/{giveaway_id}__{user_db_id}` — upserted, never duplicated.
  `all_prizes` ("everything, including prizes added later") or `prize_ids`,
  server-computed `entries` + `breakdown`, and a `user` snapshot
  (`name, nickname, profile_image, github, email`) so the admin list needs
  zero profile reads.

## Routes (`/api/giveaways`)

| Route | Auth | |
|---|---|---|
| `GET /open` | user | open campaigns + closed/drawn ones the caller entered, each with `my_entry`/`my_win`, plus top-level `my_weight` (current entries before saving) |
| `POST /<id>/entry` | user | body `{all_prizes, prize_ids}`; 409 `giveaway_closed`, 400 `invalid_prizes` / `no_prizes_selected`. **POST, not PUT** — the CORS config allows GET/POST/PATCH/DELETE only |
| `DELETE /<id>/entry` | user | withdraw (409 once closed) |
| `GET /admin`, `POST /admin`, `GET|PATCH /admin/<id>` | `volunteer.admin` | campaigns; a prize with a `won` result can't be removed or shrunk below it (409 `prize_has_winner`); status is locked once a draw starts (409 `draw_started`) |
| `GET /admin/<id>/entries` | `volunteer.admin` | ONE `where giveaway_id ==` query |
| `POST /admin/<id>/draw/start` | `volunteer.admin` | `{seed}`; status → `closed`; same seed = resume, different = 409 `draw_already_started` |
| `POST /admin/<id>/draw/results` | `volunteer.admin` | `{prize_id, slot, user_id, outcome, attempt}` and/or `{finish: true}`; transactional checks: entered that prize, no prior win (409 `already_won`), slot free (409 `slot_filled`), slot < quantity. All slots won or `finish` → `drawn` |
| `POST /admin/<id>/draw/undo` | `volunteer.admin` | `{result_id}`; a `drawn` giveaway with an empty slot goes back to `closed` |
| `POST /admin/<id>/draw/reset` | `volunteer.admin` | clears `draw`, status `closed` |

Errors are `{"error": <code>, "message": <copy>}`.

## Rules

- **Weight** = 5 × filled `GIVEAWAY_PROFILE_FIELDS` (12 fields everyone can
  fill — no apt/suite, company, Instagram, stickers or propel_id; twin of the
  frontend's `src/components/Profile/profileFields.js` — keep in sync) + GitHub
  contributions (commits + PRs + issues + reviews, same data as
  `GET /api/messages/profile/github/<login>`) capped at 45. Profile entries are
  uncapped. A GitHub failure never fails the entry: the previous stored GitHub
  entries (or 0) are used and the response says `github_unavailable: true`.
- **The winner is picked on the frontend** by a pure seeded function
  (`src/lib/giveawayDraw.js`); admins are trusted, the seed makes it
  reproducible, and the server validates every recorded result.
- Campaign reads are cached 10s (`_all_campaigns`, `read_after_write` in `common/utils/cache_registry.py`), cleared on every admin
  write. Entries are never cached.
- Slack audit on create, draw start, each winner, reset — not on user entries.
- Prize images upload through `POST /api/messages/upload-image` with
  `directory=giveaways/<id>`; admins pass `authorize_upload_directory` for any
  directory, so no extra route was needed.

Tests: `api/giveaways/tests/` (FakeDb + stubbed resolver/GitHub/transaction).
