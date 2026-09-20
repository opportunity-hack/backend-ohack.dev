# Submissions API

Team project write-ups + submission deadlines. Introduced Sep 2026 so
ohack.dev's team dashboard can replace DevPost's project page and deadline
mechanics (judging itself is unchanged — see `api/judging/`).

## Base URL
`/api`

## Ownership

- `api.submissions.submissions_service` owns SELF-SERVE, deadline-aware
  writes to a team's `project_*` fields (and now also the pre-existing
  `/team/<id>/devpost` and `/team/<id>/demo-video` routes, via
  `self_serve_team_edit`).
- `api.teams.teams_service.edit_team` remains the ADMIN write path (no
  deadline gate, no membership check — gated at the route by
  `volunteer.admin`). An admin can still override `project_*` fields
  (including `project_submission_status`) through `PATCH /api/team/edit`.

Every write here goes through `_authorize_team_write`, which:
1. 404s if the team doesn't exist.
2. 403s `{"error": "not_team_member"}` if the caller isn't on the team and
   isn't an admin (`is_admin(auth_user)` from
   `services.hackathon_planning_service`).
3. Computes the event's submission window and, unless the caller is an admin
   or the route opted out (`enforce_deadline=False`, used only by
   mentor-availability), 409s `{"error": "submissions_closed", "deadline",
   "late_until", "now"}` once it's closed.

## Data model

New optional keys on `teams/{id}` (absent ⇒ legacy team, no dashboard
UI implied): `project_tagline`, `project_story`, `project_built_with`,
`project_links`, `project_thumbnail_url`, `project_images`,
`project_updated_at`, `project_submitted_at`, `project_submission_status`
(`draft|submitted|late`), `mentor_help_wanted` (absent ⇒ treated as `True`).

New optional key on `hackathons/{id}`: `deadlines` — see
`common.utils.validators.validate_deadlines` for the shape and
`services.hackathons_service.save_hackathon` for how a `None` value becomes a
Firestore `DELETE_FIELD` on update (or is simply omitted on create).

## Endpoints

### `POST /api/team/<teamid>/project`
Member (or admin) only. Partial update — only keys present in the body are
validated and written. 400 `{"error": "invalid_project", "errors": [...]}`
on a bad field, 403/409 per `_authorize_team_write`. Sets
`project_submission_status = "draft"` on the very first save (never regressed
by a hacker's own write afterward). Returns
`{"success": true, "team": <fresh team>, "window": <submission window>}`.

### `POST /api/team/<teamid>/project/submit`
Member (or admin) only. Requires `project_tagline` and `project_story` to
already be saved (400 `{"error": "incomplete", "missing": [...]}` otherwise).
Idempotent — resubmitting returns `{"success": true, "already_submitted":
true, "team": ...}`. Sets `project_submission_status` to `"submitted"` (window
open/no-deadline) or `"late"` (window in its late-grace period, or an admin
forcing a submission through after full close).

### `POST /api/team/<teamid>/mentor-availability`
Member (or admin) only. Body `{"open": bool}`. No deadline gate — a team can
flip this any time. Signal only; changes no other backend behaviour.

### `POST /api/team/<teamid>/devpost`, `POST /api/team/<teamid>/demo-video`
(Defined in `api/teams/teams_views.py`, delegate to
`self_serve_team_edit` here.) **Security fix (Part 9 bug #1):** these used to
call `edit_team` directly with no membership check at all — any logged-in
user could overwrite any team's DevPost link or demo video. They now run
through the same `_authorize_team_write` gate as everything else in this
module.

### `GET /api/hackathons/<event_id>/submissions/window`
Public. `{"state": "open"|"late"|"closed"|"no_deadline", "submission",
"late_until", "now", "timezone"}`. Backs the dashboard's deadline strip and
the admin `DeadlinesSection` preview — the frontend should treat the server's
`now`/`state` as authoritative rather than deriving state from `deadlines`
client-side, though `deriveVoteWindow`-style client mirroring is fine for a
non-authoritative live countdown between polls.

## Sanitization

`project_tagline` / `project_story` are stored as raw markdown. The frontend
renders them with `react-markdown` **without** `rehype-raw`, so any HTML tag
in the stored text is already inert on read — `sanitize_markdown` (in
`common.utils.validators`) is defence-in-depth only: it strips a small
denylist of tags (`script|iframe|object|embed|style|link|meta|form|base`),
`on*=` attributes, and neutralizes `javascript:`/`vbscript:`/`data:` link
targets. It deliberately preserves generic `<` — e.g. `List<String>` in a
project story survives untouched.

## Images

`project_thumbnail_url` / `project_images[]` must be URLs already on the
site's own CDN under `teams/<team_id>/` (uploaded via the existing
`POST /api/messages/upload-image` with `directory=teams/<team_id>/project` —
there is no separate signed-URL mint for this). A URL already saved on the
team's doc is trusted without re-verifying against GCS; a new one must exist,
be an image, and be ≤5MB (`common.utils.cdn.get_blob_metadata`).

## Tests
```
ENVIRONMENT=test pytest api/submissions/tests
```
