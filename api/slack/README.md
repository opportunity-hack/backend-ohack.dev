# Slack API Documentation

## Base URL
`/api`

## Authentication
All endpoints require user authentication.

## Endpoints

### Get Active Slack Users
**GET** `/api/slack/users/active`

Get list of active Slack users within a specified time period.

#### Authentication Required
- User must be authenticated

#### Query Parameters
- `active_days` (integer, optional): Number of days to look back for activity (1-365, default: 30)
- `include_presence` (boolean, optional): Include current presence information (default: false)
- `minimum_presence` (string, optional): Filter by minimum presence status ('active' or 'away')

#### Response (Success)
```json
{
  "success": true,
  "count": 25,
  "users": [
    {
      "id": "U123456789",
      "name": "John Doe",
      "display_name": "john",
      "real_name": "John Doe",
      "email": "john@example.com",
      "last_activity": "2024-01-15T10:30:00Z",
      "presence": "active"
    }
  ]
}
```

#### Error Responses
```json
{
  "success": false,
  "error": "active_days parameter must be between 1 and 365"
}
```
**Status Code**: 400

```json
{
  "success": false,
  "error": "Failed to retrieve active Slack users"
}
```
**Status Code**: 500

---

### Get Slack User Details
**GET** `/api/slack/users/{user_id}`

Get detailed information about a specific Slack user.

#### Path Parameters
- `user_id` (string): Slack user ID

#### Authentication Required
- User must be authenticated

#### Response (Success)
```json
{
  "success": true,
  "user": {
    "id": "U123456789",
    "name": "John Doe",
    "display_name": "john",
    "real_name": "John Doe",
    "email": "john@example.com",
    "profile": {
      "avatar_url": "https://example.com/avatar.jpg",
      "status_text": "Working on hackathon project",
      "title": "Software Engineer"
    },
    "is_admin": false,
    "is_bot": false,
    "timezone": "America/New_York"
  }
}
```

#### Error Responses
```json
{
  "success": false,
  "error": "User with ID U123456789 not found"
}
```
**Status Code**: 404

```json
{
  "success": false,
  "error": "Failed to retrieve details for Slack user U123456789"
}
```
**Status Code**: 500

---

### Clear Slack Cache (Admin)
**POST** `/api/slack/cache/clear`

Clear all Slack-related caches.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Response (Success)
```json
{
  "success": true,
  "message": "Slack cache cleared successfully"
}
```

#### Error Responses
```json
{
  "success": false,
  "error": "Insufficient permissions"
}
```
**Status Code**: 403

```json
{
  "success": false,
  "error": "Failed to clear Slack cache"
}
```
**Status Code**: 500

---

### Send Slack Message (Admin)
**POST** `/api/slack/message` — `volunteer.admin`, header `X-Org-Id`.

```json
{ "message": ":trophy: Update with a <https://www.ohack.dev|link>", "channel": "general", "unfurl_links": false }
```

- `message` (required): Slack mrkdwn, up to 12,000 characters.
- `channel`: channel name (with or without `#`), channel id, or member id for a direct message. Not needed with `to_self`.
- `to_self: true`: sends a direct message to the signed-in admin (a test send).
- `unfurl_links`, `username`, `icon_emoji`: optional.

Success: `{ "success": true, "channel": "#general", "channel_id": "C…", "ts": "…", "permalink": "https://…" }`

Failure: `{ "success": false, "error": "<code>", "message": "<what to do>" }` with status 400 (`message_required`, `channel_required`, `invalid_channel`, `message_too_long`), 404 (`channel_not_found`, `no_slack_account`), 409 (`not_in_channel`, `is_archived`), 429 (`ratelimited`) or 502 (any other Slack refusal).

### List Channels (Admin)
**GET** `/api/slack/admin/channels` — `volunteer.admin`, header `X-Org-Id`.

`{ "success": true, "channels": [{ "id", "name", "is_private", "is_member", "num_members" }] }` — public channels plus the private ones the bot is in, sorted by name.
