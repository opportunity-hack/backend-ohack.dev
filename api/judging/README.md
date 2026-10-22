# Judging API Documentation

## Base URL
`/api/judge`

## Authentication
All endpoints require user authentication. Admin endpoints require additional permissions.

## Judge Scoring Endpoints

### Get Judge Assignments
**GET** `/api/judge/assignments/{judge_id}`

Get all hackathon assignments for a specific judge.

#### Path Parameters
- `judge_id` (string): Judge user ID

#### Authentication
- User must be authenticated
- Judge can only access their own assignments

#### Response
```json
{
  "assignments": [
    {
      "id": "assignment_id",
      "event_id": "hackathon_id",
      "event_name": "Hackathon 2024",
      "team_id": "team_id",
      "team_name": "Team Alpha",
      "round": "semifinal",
      "demo_time": "2024-01-15T14:30:00Z",
      "room": "Room A"
    }
  ]
}
```

---

### Get Teams for Judge
**GET** `/api/judge/teams/{judge_id}/{event_id}`

Get teams assigned to a judge for a specific hackathon.

#### Path Parameters
- `judge_id` (string): Judge user ID
- `event_id` (string): Hackathon event ID

#### Authentication
- User must be authenticated
- Judge can only access their own teams

#### Response
```json
{
  "teams": [
    {
      "id": "team_id",
      "name": "Team Alpha",
      "members": ["user1", "user2"],
      "project_description": "AI-powered solution",
      "demo_time": "2024-01-15T14:30:00Z",
      "room": "Room A",
      "devpost_link": "https://devpost.com/project"
    }
  ]
}
```

---

### Get Team Details
**GET** `/api/judge/team/{team_id}`

Get detailed information about a specific team for judging.

#### Path Parameters
- `team_id` (string): Team ID

#### Authentication
- User must be authenticated
- Judge must be assigned to this team

#### Response
```json
{
  "team": {
    "id": "team_id",
    "name": "Team Alpha",
    "description": "Project description",
    "members": [
      {
        "id": "user1",
        "name": "John Doe",
        "role": "Developer"
      }
    ],
    "github_repo": "https://github.com/org/repo",
    "devpost_link": "https://devpost.com/project",
    "demo_time": "2024-01-15T14:30:00Z",
    "room": "Room A"
  }
}
```

---

### Submit Score
**POST** `/api/judge/score`

Submit or update a team score.

#### Authentication
- User must be authenticated
- Judge must be assigned to the team

#### Request Body
```json
{
  "judge_id": "judge_user_id",
  "team_id": "team_id", 
  "event_id": "event_id",
  "round": "semifinal",
  "scores": {
    "technical_execution": 8,
    "innovation": 9,
    "presentation": 7,
    "market_potential": 6
  },
  "feedback": "Great technical implementation, could improve presentation",
  "submitted_at": "2024-01-15T15:30:00Z"
}
```

#### Required Fields
- `judge_id`, `team_id`, `event_id`, `round`, `scores`

#### Response
```json
{
  "success": true,
  "message": "Score submitted successfully",
  "score_id": "score_id",
  "total_score": 30
}
```

---

### Save Draft Score
**POST** `/api/judge/draft`

Save draft scores for auto-save functionality.

#### Request Body
Same as submit score, but saves as draft.

#### Response
```json
{
  "success": true,
  "message": "Draft saved successfully",
  "draft_id": "draft_id"
}
```

---

### Get Draft Score
**GET** `/api/judge/draft`

Get draft scores for a judge, team, event, and round.

#### Authentication
- User must be authenticated
- Judge must be assigned to the team

#### Query Parameters
- `judge_id` (string, required): Judge user ID
- `team_id` (string, required): Team ID
- `event_id` (string, required): Event ID
- `round` (string, required): Round name

#### Response
```json
{
  "draft": {
    "id": "draft_id",
    "judge_id": "judge_id",
    "team_id": "team_id",
    "event_id": "event_id",
    "round": "semifinal",
    "scores": {
      "technical_execution": 8,
      "innovation": 9,
      "presentation": null,
      "market_potential": null
    },
    "total_score": null,
    "feedback": "Work in progress...",
    "updated_at": "2024-01-15T15:30:00Z",
    "created_at": "2024-01-15T15:25:00Z"
  }
}
```

If no draft exists:
```json
{
  "draft": null
}
```

---

### Get Judge Scores
**GET** `/api/judge/scores/{judge_id}/{event_id}`

Get all scores submitted by a judge for a hackathon.

#### Response
```json
{
  "scores": [
    {
      "team_id": "team_id",
      "team_name": "Team Alpha",
      "round": "semifinal",
      "scores": {
        "technical_execution": 8,
        "innovation": 9
      },
      "total_score": 30,
      "submitted_at": "2024-01-15T15:30:00Z"
    }
  ]
}
```

---

### Get Individual Score
**GET** `/api/judge/score/{judge_id}/{team_id}/{event_id}/{round_name}`

Get a specific judge score for a team.

#### Query Parameters
- `draft` (boolean): Get draft version if true

#### Response
```json
{
  "score": {
    "judge_id": "judge_id",
    "team_id": "team_id", 
    "round": "semifinal",
    "scores": {
      "technical_execution": 8,
      "innovation": 9
    },
    "feedback": "Great work!",
    "is_draft": false,
    "submitted_at": "2024-01-15T15:30:00Z"
  }
}
```

---

## Admin Assignment Management

### Create Judge Assignment (Admin)
**POST** `/api/judge/assignments`

Create a new judge assignment.

#### Authentication
- Admin permissions required (`judge.admin`)

#### Request Body
```json
{
  "judge_id": "judge_user_id",
  "event_id": "event_id",
  "team_id": "team_id",
  "round": "semifinal",
  "demo_time": "2024-01-15T14:30:00Z",
  "room": "Room A"
}
```

#### Response
```json
{
  "success": true,
  "assignment_id": "assignment_id",
  "message": "Assignment created successfully"
}
```

---

### Update Assignment (Admin)
**PUT** `/api/judge/assignments/{assignment_id}`

Update judge assignment details.

#### Request Body
```json
{
  "demo_time": "2024-01-15T15:00:00Z",
  "room": "Room B"
}
```

---

### Delete Assignment (Admin)  
**DELETE** `/api/judge/assignments/{assignment_id}`

Remove a judge assignment.

---

## Judge Panel Management

### Get Judge Panels (Admin)
**GET** `/api/judge/panels/{event_id}`

Get all judge panels for an event.

#### Response
```json
{
  "panels": [
    {
      "id": "panel_id",
      "panel_name": "Technical Panel",
      "room": "Room A",
      "judges": ["judge1", "judge2"],
      "event_id": "event_id"
    }
  ]
}
```

---

### Create Judge Panel (Admin)
**POST** `/api/judge/panels`

Create a new judge panel.

#### Request Body
```json
{
  "event_id": "event_id",
  "panel_name": "Technical Panel",
  "room": "Room A",
  "judge_ids": ["judge1", "judge2"]
}
```

---

### Update Judge Panel (Admin)
**PUT** `/api/judge/panels/{panel_id}`

Update judge panel details.

---

### Delete Judge Panel (Admin)
**DELETE** `/api/judge/panels/{panel_id}`

Remove a judge panel.

## Features

1. **Judge Assignment System**: Assign judges to specific teams and rounds
2. **Multi-Round Scoring**: Support for different judging rounds (preliminary, semifinal, final)
3. **Flexible Scoring Criteria**: Configurable scoring categories and weights
4. **Draft Auto-Save**: Prevent data loss with draft functionality
5. **Judge Panel Management**: Organize judges into panels by room/expertise
6. **Access Control**: Judges can only see their assigned teams
7. **Admin Tools**: Comprehensive admin interface for assignment management

## Notes for Frontend Development

1. **Authentication**: All endpoints require authentication
2. **Access Control**: Judges can only access their own data
3. **Admin Permissions**: Panel and assignment management requires `judge.admin`
4. **Content-Type**: POST/PUT requests use `application/json`
5. **Score Structure**: Flexible scoring object with configurable criteria
6. **Draft System**: Implement auto-save for better user experience
7. **Real-time Updates**: Consider refreshing data during active judging
8. **Validation**: Validate score ranges and required fields
9. **Error Handling**: Check for assignment verification errors
10. **Round Management**: Support multiple judging rounds per event
11. **Panel UI**: Build admin interface for panel and assignment management
12. **Feedback**: Support rich text feedback from judges

---

## Round Locks (Oct 2026)

Admins can close scoring for a round. While locked, `POST /api/judge/score` and `POST /api/judge/draft` return **409**:

```json
{"error": "round_locked", "round": "round2", "event_id": "fall-2026", "message": "Scoring for this round is closed. Your score was not changed."}
```

### Get locks
**GET** `/api/judge/locks/{event_id}` — any authenticated user.

```json
{"locks": {"round1": {"locked": false, "locked_at": null, "updated_at": null}, "round2": {"locked": true, "locked_at": "2026-10-11T17:40:00-07:00", "updated_at": "2026-10-11T17:40:00-07:00"}}}
```

This is the public view: it never includes who locked the round. Admins see `locked_by` / `locked_by_name` in the progress response below.

`GET /api/judge/teams/{judge_id}/{event_id}` also embeds the same `locks` object and an `event` summary (`title`, `start_date`, `end_date`, `timezone`).

### Set a lock (Admin)
**POST** `/api/judge/admin/locks/{event_id}/{round_name}` — `judge.admin`. Body `{"locked": true}` (JSON boolean required). Returns `{"success": true, "lock": {...}}`.

## Judge Progress (Admin)
**GET** `/api/judge/admin/progress/{event_id}/{round_name}` — `judge.admin`.

One call that answers "who has sent in scores and who hasn't":

```json
{
  "lock": {"locked": false},
  "judges": [{"judge_id": "…", "name": "Ada", "email": "…", "assigned": 5, "submitted": 3, "complete": false,
              "assigned_team_ids": ["…"], "submitted_team_ids": ["…"], "draft_team_ids": ["…"], "missing_team_ids": ["…"],
              "last_submitted_at": "…"}],
  "teams": [{"team_id": "…", "name": "Alpha", "assigned": 15, "submitted": 12, "assigned_judge_ids": ["…"], "submitted_judge_ids": ["…"]}],
  "summary": {"judges_total": 15, "judges_complete": 9, "assignments_total": 75, "submitted_total": 58, "drafts_total": 4, "orphan_submitted": 0}
}
```

Incomplete judges sort first. A draft that has since been submitted is not counted as a draft.

## Bulk Assignments (Admin)
**POST** `/api/judge/admin/assignments/bulk` — `judge.admin`.

```json
{"event_id": "fall-2026", "round": "round2", "panel_id": "<panel doc id>", "judge_ids": ["…"], "team_ids": ["…"],
 "room": "Main Room", "demo_times": {"<team_id>": "2026-10-11T16:40:00-07:00"}, "replace": true}
```

Creates every missing (judge, team) pair for the panel in one request (idempotent). `replace: true` (JSON boolean required) also deletes the panel's pairs that are no longer in the cross product and collapses duplicate docs. Scores are never modified. Validation (400): `replace_must_be_boolean`, `empty_cross_product` (both lists must be non-empty — an empty set with replace would wipe the panel), `ids_must_be_non_empty_strings`, `room_must_be_string`, `demo_times_must_map_team_id_to_iso_string`. Response: `{"success": true, "created": 12, "kept": 63, "updated": 0, "removed": 3, "total": 75}`.

### Assignment check on writes
`POST /api/judge/score` and `POST /api/judge/draft` require an assignment for the judge **in that event and that round** (403 otherwise). Reads of a team (`GET /api/judge/team/<id>`) still only require an assignment for the team in any round.
