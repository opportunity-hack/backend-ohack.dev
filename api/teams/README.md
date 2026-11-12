# Teams API Documentation

## Base URL
`/api/team`

## Authentication
All endpoints require user authentication. Admin endpoints require additional permissions.

## Endpoints

### Get Teams by Hackathon
**GET** `/api/team/{hackathon_id}`

Get all teams for a specific hackathon.

#### Path Parameters
- `hackathon_id` (string): Hackathon ID

#### Authentication Required
- User must be authenticated

#### Response
```json
{
  "teams": [
    {
      "id": "team_id",
      "name": "Team Name",
      "description": "Team description",
      "members": ["user1", "user2"],
      "status": "NONPROFIT_SELECTED",
      "active": true,
      "hackathon_id": "hackathon_id"
    }
  ]
}
```

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Get My Teams by Event
**GET** `/api/team/{event_id}/me`

Get teams that the authenticated user is part of for a specific event.

#### Path Parameters
- `event_id` (string): Event ID

#### Authentication Required
- User must be authenticated

#### Response
Public team view (staff-only fields such as `nonprofit_rankings`,
`admin_notes`, `comments` stripped; no `users[]`). `problem_statements`
(DocumentReferences) is replaced by `project`: the project the team was
approved for, or `null` before approval.
```json
{
  "teams": [
    {
      "id": "team_id",
      "name": "My Team",
      "status": "NONPROFIT_SELECTED",
      "project": {"id": "ps_id", "title": "Volunteer scheduler"}
    }
  ],
  "user_id": "oauth2|slack|T1-U1"
}
```

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Project Options (team creation)
**GET** `/api/team/{event_id}/project-options`

Projects a team can rank when it's created, grouped by nonprofit. ONE rule
shared with `POST /api/team/queue` (it validates `nonprofitRankings` against
this same function): the event's `nonprofits` → their `problem_statements`,
filtered by the event's `visible_problem_statements` allowlist (missing/null =
all; an empty list hides everything), `status == "production"` dropped,
nonprofits with no selectable project dropped. 60s cache (cleared by admin
writes). `description` is trimmed to ~400 chars.

#### Authentication Required
- User must be authenticated

#### Response
```json
{
  "event_id": "fall-2026",
  "nonprofits": [
    {
      "id": "npo_id", "name": "Alpha", "image": "https://…", "website": "https://…",
      "projects": [
        {"id": "ps_id", "title": "Volunteer scheduler", "description": "…", "skills": ["react"], "status": "hackathon"}
      ]
    }
  ]
}
```

#### Error Response
`404 {"error": "event_not_found", "message": "…"}`

---

### Edit Team (Admin)
**PATCH** `/api/team/edit`

Edit team details (admin only).

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "id": "team_id",
  "name": "Updated Team Name",
  "description": "Updated description",
  "status": "NONPROFIT_SELECTED",
  "problem_statement_id": "ps_id"
}
```

`problem_statement_id` moves the team to another project: it must be one of
the nonprofit's projects (`selected_nonprofit_id` from the same body, else the
stored one) and writes `problem_statements: [ref]`; a falsy value clears it.
Refusals: `400 {error: nonprofit_not_found | no_problem_statements |
invalid_problem_statement | missing_fields, message}`.

#### Response
`{"message", "success": true, "team_id"}`.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Add Devpost Link
**POST** `/api/team/{teamid}/devpost`

Add a Devpost link to a team.

#### Path Parameters
- `teamid` (string): Team ID

#### Authentication Required
- User must be authenticated

#### Request Body (JSON)
```json
{
  "devpost_link": "https://devpost.com/software/project-name"
}
```

#### Response
Returns updated team object with Devpost link.

#### Error Responses
```json
{
  "error": "Devpost link is required"
}
```
**Status Code**: 400

```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Add Team Member (Admin)
**POST** `/api/team/{teamid}/member`

Add a member to a team (admin only).

#### Path Parameters
- `teamid` (string): Team ID

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "id": "user_id_to_add"
}
```

#### Response
Returns updated team object.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Delete Team (Admin)
**DELETE** `/api/team/{teamid}`

Delete a team (admin only).

#### Path Parameters
- `teamid` (string): Team ID

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Response
Returns deletion confirmation.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Remove Team Member (Admin)
**DELETE** `/api/team/{teamid}/member`

Remove a member from a team (admin only).

#### Path Parameters
- `teamid` (string): Team ID

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "id": "user_id_to_remove"
}
```

#### Response
Returns updated team object.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Queue Team
**POST** `/api/team/queue`

Create a team (status `IN_REVIEW`, `active: false`) for organizer review.
Approved hackers only (`membership_rules.check_team_queue`, 403
`not_approved_hacker`). Every refusal below happens BEFORE the Slack channel
is created.

#### Authentication Required
- User must be authenticated

#### Request Body (JSON)
```json
{
  "name": "Team Name",
  "slackChannel": "team-channel",
  "eventId": "fall-2026",
  "githubUsername": "octocat",
  "comments": "optional",
  "teamMembers": [{"id": "U123", "name": "ada", "real_name": "Ada"}, "free-text name"],
  "nonprofitRankings": [
    {"nonprofit_id": "npo_id", "problem_statement_id": "ps_id", "rank": 1},
    {"nonprofit_id": "npo_id", "problem_statement_id": "ps_id_2", "rank": 2}
  ]
}
```

`nonprofitRankings` = one entry per PROJECT (a nonprofit may repeat). Each
`problem_statement_id` must be a selectable project of that `nonprofit_id` in
this event (see Project Options). 2–5 entries, or every available project when
the event has fewer than 2. Duplicates are dropped and ranks reassigned 1..n;
the normalized list is stored as `nonprofit_rankings` (staff-only — stripped
from every public view). **Legacy shape** `[{nonprofit_id, rank}]` (no entry
carries `problem_statement_id`) is still accepted — each nonprofit must be in
the event, no count rule — so the pre-release frontend keeps working between
deploys; mixing the two shapes is refused.

#### Response
`{"message", "success": true, "team"}`

#### Error Responses (all `400 {error, message, success: false}`)
- `missing_fields` — name / slackChannel / eventId / githubUsername
- `event_not_found`
- `invalid_rankings` — too few / too many / project not selectable / project of another nonprofit / mixed shapes
- `invalid_github_username` (was a 200 before Nov 2026)
- `user_not_found` — the creator has no `users` doc yet (used to crash after the team was inserted)

---

### Approve Team (Admin)
**POST** `/api/team/approve`

Approve a team for an exact project. Sets status **`NONPROFIT_SELECTED`**
(there is no `APPROVED` status), `active: "True"`, `selected_nonprofit_id`,
`problem_statements: [ref]`, `github_links`, `approved_by/at`; creates the
GitHub repo (README seeded with THAT project), posts the approval to Slack
(names the project), mints the AI gateway keys and appends the repo to the
project's `github` list (so `/project/<id>` shows it).

The team is first claimed in a Firestore transaction (`IN_REVIEW` →
transient `APPROVING`, with `approval_started_at/by`), so two admins approving
at once can't both create resources; a failure before the final write puts it
back to `IN_REVIEW`. A claim older than 10 minutes is treated as abandoned and
can be taken over.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "teamId": "team_id",
  "nonprofitId": "nonprofit_id",
  "problemStatementId": "ps_id"
}
```
`problemStatementId` is required when the nonprofit has more than one
project; a single-project nonprofit auto-fills it.

#### Response
`{"message", "success": true, "team", "problem_statement_id"}`

#### Error Responses (`{error, message, success: false}`)
| Status | `error` |
|---|---|
| 400 | `missing_fields`, `nonprofit_not_found`, `no_problem_statements`, `problem_statement_required`, `invalid_problem_statement`, `team_has_no_members`, `event_not_found`, `github_org_missing` |
| 404 | `team_not_found` |
| 409 | `already_approved` (approved, or another admin's claim is in progress), `not_in_review` (e.g. INACTIVE) |
| 502 | `github_repo_failed` (team goes back to `IN_REVIEW`) |

---

### Send Team Message (Admin)
**POST** `/api/team/admin/{teamid}/message`

Send a message to a team via Slack/email (admin only).

#### Path Parameters
- `teamid` (string): Team ID

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "message": "Message content",
  "channel": "slack",
  "subject": "Message subject"
}
```

#### Response
Returns message delivery status.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Get Queued Teams (Admin)
**GET** `/api/team/queue`

Get all teams in the approval queue (admin only).

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Response
```json
{
  "queued_teams": [
    {
      "id": "team_id",
      "name": "Team Name",
      "status": "IN_REVIEW",
      "submitted_at": "2024-01-15T10:30:00Z",
      "members": ["user1", "user2"]
    }
  ]
}
```

## Features

1. **Team Management**: Complete CRUD operations for teams
2. **Nonprofit Matching**: Queue and approval system for nonprofit assignments
3. **Member Management**: Add/remove team members
4. **Devpost Integration**: Link teams to their Devpost submissions
5. **Admin Controls**: Comprehensive admin tools for team oversight
6. **Communication**: Send messages to teams via multiple channels
7. **Status Tracking**: Track team status through approval workflow

## Notes for Frontend Development

1. **Authentication**: All endpoints require user authentication
2. **Admin Permissions**: Many endpoints require `volunteer.admin` permission
3. **Organization Context**: Admin operations require `X-Org-Id` header
4. **Content-Type**: POST/PATCH/DELETE requests should use `application/json`
5. **Team Status**: Teams progress IN_REVIEW → (transient APPROVING) → NONPROFIT_SELECTED on approval; there is no `APPROVED` status
6. **Member Arrays**: Team members are represented as arrays of user IDs
7. **Devpost Validation**: Frontend should validate Devpost URL format
8. **Error Handling**: Check for "Unauthorized" errors and redirect to login
9. **Real-time Updates**: Consider refreshing data after admin actions
10. **Workflow UI**: Build admin interface for team approval workflow
11. **Communication Tools**: Integrate messaging features for team coordination
12. **GitHub Integration**: Teams get GitHub repositories upon approval