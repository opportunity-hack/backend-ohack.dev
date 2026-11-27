# Participation letters (`api/letters/`, Nov 2026)

Self-serve, signed PDF letters for judges, mentors, volunteers and hackers.
The person opens `/profile` → Volunteer History, and if they qualify for an
event they download a signed letter immediately. No approval step; admins can
preview any letter and revoke one after the fact.

## Modules

| File | What |
|---|---|
| `letter_facts.py` | `build_facts(user, event_id, role)` → every value the template prints, or `NotEligible(reason)`. Eligibility rules, location/date parsing, `EventContext` (one read of the event, teams, members, scores). |
| `letter_pdf.py` | The ONE template. `compose_letter(facts)` (locked wording, sentence-drop) + `render_pdf(facts, letter_id=, preview=)` (ReportLab). Org block, signer config, fonts. |
| `letters_service.py` | Firestore `letters`, issue / stream / verify / admin list, preview, revoke, exception. |
| `letters_views.py` | Routes. |
| `assets/` | Vendored logo (`logo_banner.png`, the Navbar banner), Noto Serif/Sans + Allura TTFs and their OFL licenses. Nothing is fetched at render time. |

## Eligibility (one event; `end_date >= LETTERS_START_DATE` = 2026-11-14)

| Role | Rule | Reason codes |
|---|---|---|
| judge | `isSelected` AND ≥1 submitted `judge_scores` doc for the judge at the event. Scores are keyed on the STORED judge id (`user_id` or doc id) — the caller's ids come from `api/judging/judge_identity.caller_judge_ids`. No check-in needed. | `not_selected`, `no_scores` |
| mentor | `isSelected` AND `checkInTime` (remote mentors too) | `not_selected`, `no_checkin` |
| volunteer | `isSelected` AND `checkInTime` | `not_selected`, `no_checkin` |
| hacker | `isSelected` AND on a team at the event with `active: true` (member matched by user doc id, `propel_id`/`user_id`, or email) | `not_selected`, `no_active_team` |
| all | event exists, has ended (23:59:59 `end_date`, event tz), ends on/after the start date, a name exists | `event_not_found`, `event_not_ended`, `before_start_date`, `name_missing` |

Older events: admin exception only (`POST /api/admin/letters/exception`).

## Letter text

Locked. The person sets only the name (saved to the user doc as `legal_name`)
and whether to show the "{title}, {company}" line (their profile `role` +
`company`). Every other value is stored data; a missing optional fact drops its
sentence or clause. In person vs virtual comes from the person's application
(`inPerson` / `isInPerson`) or a plainly virtual event location; unknown ⇒ the
phrase is omitted. The venue and "City, State" are parsed from the hackathon's
free-text `location` (`"<venue>, <city>, <ST>"`); anything else ⇒ omitted.
No pronouns, praise adjectives, immigration language or hours — tests check.

Names: Latin/Greek/Cyrillic/Vietnamese render in Noto Serif; CJK runs fall
back to ReportLab's built-in CID fonts (not embedded — the PDF viewer supplies
the glyphs). Anything else (emoji, Devanagari, Arabic…) is refused with
400 `name_unsupported_characters` instead of printing empty boxes.

## Data

`letters/<id>` — id = 12 random base32 chars (`secrets`). Fields: `user_id`
(PropelAuth), `email`, `event_id`, `role`, `name_on_letter`, `show_title_line`,
`facts` (frozen snapshot, no evidence), `template_version`, `signer {name,
title}`, `status` (`issued` | `revoked`), `pdf_path`, `pdf_sha256`,
`issued_at`, `revoked_by`, `revoked_at`, `revoked_reason`, `exception`,
`exception_note`, `issued_by`.

`letter_keys/<hash(user, event, role)>` — created before rendering, makes
issue idempotent (a double click gets 409 `letter_in_progress` or the same
letter, never two). A key older than 2 minutes with no letter is taken over.

`letter_rate_limits/<user_id>` — `{day, count}`; 10 issue requests per user
per UTC day (429 `rate_limited`). Firestore, so it holds across workers.

**PDF storage:** rendered once at issue, uploaded to the CDN bucket at
`letters/<token_urlsafe(24)>/<id>.pdf` (decided Nov 2026: no private bucket
exists; the path is unguessable and never returned to a client). Always
streamed back through the API; `pdf_sha256` is checked on every read.

## Routes

| Route | Auth | Notes |
|---|---|---|
| `GET /api/letters/mine` | user | `{rows: [{event_id, event_title, event_end_date, role, eligible, reason, letter_id, status, issued_at}], name_default, title_line}` — qualifying events + any letter already issued (exceptions) |
| `POST /api/letters` | user | `{event_id, role, name_on_letter, show_title_line}` → 201 `{letter_id, status, created}` (200 when it already existed). 409 `not_eligible` + `reason`, 409 `letter_revoked`, 400 `name_missing` / `name_unsupported_characters`, 429. |
| `GET /api/letters/preview?event_id=&role=&name=&show_title_line=true` | user | Watermarked "PREVIEW, NOT VALID" PDF, no signature / ID / verify line. Caller's own facts only. |
| `GET /api/letters/<id>/pdf[?download=1]` | owner or `volunteer.admin` | Issued only (410 when revoked). Same bytes every time. |
| `GET /api/letters/verify/<id>` | public | Only `name_on_letter, role, event_title, served_date, issued_at, status`. 404 unknown. |
| `GET /api/admin/letters?event_id=&status=issued\|revoked\|not_issued&role=` | `volunteer.admin` | One row per person + role at the event with eligibility, reason and `evidence {score_count, check_in_time, team_name, is_selected}`, plus letter id/status/facts. |
| `GET /api/admin/letters/preview?user_id=&event_id=&role=` | `volunteer.admin` | Stored PDF once issued; else a watermarked render with the rules off. Headers `X-Letter-Status`, `X-Letter-Eligible`, `X-Letter-Reason`, `X-Letter-Id`. |
| `POST /api/admin/letters/<id>/revoke` `{reason}` | `volunteer.admin` | Reason required. |
| `POST /api/admin/letters/exception` `{user_id \| email, event_id, role, note}` | `volunteer.admin` | Skips the start date and eligibility, snapshots what exists, `exception: true`. 409 `already_issued`. |

Slack audit (`send_slack_audit`) on issue, revoke and exception. No email on
issue — the person already has the PDF.

## Config

`LETTERS_SIGNER_NAME`, `LETTERS_SIGNER_TITLE`, `LETTERS_SIGNATURE_IMAGE`
(path to a PNG; replaces the typeset Allura signature), `LETTERS_VERIFY_BASE_URL`
(default `https://www.ohack.dev/letters/verify`).

## Checking the layout

`python scripts/render_sample_letters.py [--preview] [--out DIR]` renders one
letter per role from fixture facts (no Firestore) into `/tmp/ohack-letters`.
Samples from the first release live in `docs/letters/samples/`.

Tests: `api/letters/tests/` (facts, template, service on an in-memory
Firestore, routes with `common.auth` stubbed).
