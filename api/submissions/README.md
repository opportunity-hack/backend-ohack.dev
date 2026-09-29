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
  `self_serve_team_edit`). `self_serve_team_edit` also busts the
  `services.hackathons_service` event cache (via this module's `clear_cache`)
  after delegating to `edit_team`, which on its own only clears the generic
  per-function caches — without this the event page kept showing a stale
  DevPost link / demo video for up to 10 minutes after a self-serve save.
- `api.teams.teams_service.edit_team` remains the ADMIN write path (no
  deadline gate, no membership check — gated at the route by
  `volunteer.admin`). An admin can override `project_*` fields through
  `PATCH /api/team/edit`, including `project_submission_status` — validated
  against `draft|submitted|late` (400 on anything else, no write);
  `project_tagline`/`project_story` get the same `sanitize_markdown`
  treatment as the self-serve `save_project` path; a status *change* stamps
  `project_updated_at`.

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
Firestore `DELETE_FIELD` on update (or is simply omitted on create). **To
clear a single deadline, send `{"deadlines": {"<key>": null}}`; sending
`{"deadlines": {}}` (or a top-level `"deadlines": null`) is a no-op** — an
empty map merges zero sub-fields into the stored `deadlines` map under
`set(merge=True)`, leaving whatever was already there untouched.

`compute_submission_window` (here) and `compute_voting_window`
(`api/peer_votes/`) both re-parse stored deadline strings through
`normalize_deadline_iso` before comparing them against `now`, rather than
calling `datetime.fromisoformat` on the raw stored value directly — a naive
or `"Z"`-suffixed stored string used to raise (TypeError comparing
naive-vs-aware; `"Z"` isn't accepted by `fromisoformat` until Python 3.11,
and this backend targets 3.9) and surface as an unhandled 500 across
`/project`, `/submit`, `/devpost`, `/demo-video`, `/window`, `/slate`, and
`/ballot`. An unparseable stored value is now logged and treated as absent
(`no_deadline` for submissions, `closed` for voting) instead.

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
true, "team": ...}` **even after the submission window has fully closed**:
the already-submitted check runs before the deadline gate, not after, so a
team that submitted on time never gets a spurious 409 just by revisiting the
dashboard past close. A team that has *not yet* submitted is still blocked
with 409 `submissions_closed` once the window is fully closed, unless the
caller is an admin. Sets `project_submission_status` to `"submitted"` (window
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
`on*=` attributes (whitespace- **or** slash-preceded, so `<img/onerror=...>`
is caught too), and neutralizes `javascript:`/`vbscript:`/`data:` targets in
HTML attributes (quoted **or** unquoted) as well as in markdown link/image
syntax (`[text](javascript:...)` → `[text](#)`). The tag-strip pass loops to
a fixpoint so a nested bypass like `<scr<script>ipt>` — where a single strip
pass removes the inner `<script>` and the leftover pieces concatenate right
back into a live `<script>` tag — can't survive. It deliberately preserves
generic `<` — e.g. `List<String>`/`Map<K,V>` in a project story survives
untouched.

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
