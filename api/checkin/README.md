# Check-in API (`/api/checkin`)

Registration-desk routes behind the admin check-in workbench
(`/admin/hackathons/<event_id>?section=checkin`). Every route needs a Bearer
token + `X-Org-Id` and **either** `volunteer.admin` **or** `checkin.admin`
(`common.auth.require_any_permission`). `checkin.admin` is a PropelAuth
permission meant for desk helpers using their own phone/login: it unlocks the
first three routes and nothing else. The reset route is **`volunteer.admin` only**.

| Route | What |
|---|---|
| `GET /api/checkin/<event_id>/roster/<type>` | Lean roster for one type (`mentor|judge|volunteer|hacker|sponsor`), approved or not. Fields: `id,name,email,volunteer_type,isSelected,status,checkedIn,checkedInAt,checkedInBy,company,companyName,title,isCheckedIn,checkInTime` (the last two let the admin tell an online mentor self check-in — no `checkedInAt` — from a desk scan). |
| `GET /api/checkin/<event_id>/checkins/<type>` | Same projection, checked-in people only. `{data, count}`. |
| `POST /api/checkin/<event_id>/<type>/<volunteer_id>` | Mark checked in. Writes ONLY `checkedIn/checkedInAt/checkedInBy/updated_by/updated_timestamp`. 404 unknown id; 409 `{error:"wrong_event"}` when the doc's `event_id`/`volunteer_type` don't match the URL (the QR encodes `eventId|volunteerId|type`). Repeat ⇒ `{already_checked_in: true}`, original timestamp kept, no second Slack welcome. |

| `POST /api/checkin/<event_id>/reset/<type>` | **`volunteer.admin` only.** Body `{"volunteer_ids": [...]}` (≤500) or `{"all": true}`; anything else ⇒ 400 `missing_scope` (an empty body never resets everyone). Clears every check-in field a desk scan or a mentor self check-in writes (`checkedIn`, `isCheckedIn`, `checkInTime`, `checkedInAt`, `checkedInBy`, `timeSlot`, `checkOutTime`, `checkInDuration`, `checkInTimeList`, `checkoutTimeList`) with explicit values (no `DELETE_FIELD`), appends `{at, by, by_email, previous}` to `checkin_reset_history`, clears the hackathon TTL caches + each person's redis caches, and sends ONE `send_slack_audit(action="reset_checkins")` — nobody else is notified. Response `{reset, ids, skipped:[{id, reason: not_found|wrong_event|not_checked_in}]}`; a second reset is a no-op. |

The first check-in triggers the same Slack welcome as the legacy admin PATCH
(`services.hackathons_service.send_checkin_welcome`, shared). Unknown type ⇒ 404.
