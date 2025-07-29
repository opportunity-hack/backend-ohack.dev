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
      "status": "APPROVED",
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
```json
{
  "teams": [
    {
      "id": "team_id",
      "name": "My Team",
      "role": "member",
      "status": "APPROVED"
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
  "status": "APPROVED"
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

Submit a team for nonprofit assignment queue.

#### Authentication Required
- User must be authenticated

#### Request Body (JSON)
```json
{
  "name": "Team Name",
  "description": "Team description",
  "members": ["user1", "user2"],
  "skills": ["JavaScript", "Python"],
  "nonprofit_preference": "education"
}
```

#### Response
Returns queued team object with status "IN_REVIEW".

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

---

### Approve Team (Admin)
**POST** `/api/team/approve`

Approve a team and assign to nonprofit (admin only).

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "team_id": "team_id",
  "nonprofit_id": "nonprofit_id",
  "github_repo": "https://github.com/org/repo"
}
```

#### Response
Returns approved team object with status "APPROVED" and active=true.

#### Error Response
```json
{
  "error": "Unauthorized"
}
```
**Status Code**: 401

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
5. **Team Status**: Teams progress from queue → IN_REVIEW → APPROVED
6. **Member Arrays**: Team members are represented as arrays of user IDs
7. **Devpost Validation**: Frontend should validate Devpost URL format
8. **Error Handling**: Check for "Unauthorized" errors and redirect to login
9. **Real-time Updates**: Consider refreshing data after admin actions
10. **Workflow UI**: Build admin interface for team approval workflow
11. **Communication Tools**: Integrate messaging features for team coordination
12. **GitHub Integration**: Teams get GitHub repositories upon approval