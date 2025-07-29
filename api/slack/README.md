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
**POST** `/api/slack/message`

Send a message to a Slack channel or user.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "message": "Hello from the hackathon platform!",
  "channel": "#general",
  "username": "Hackathon Bot",
  "icon_emoji": ":robot_face:"
}
```

#### Required Fields
- `message` (string): The message to send
- `channel` (string): Channel name (with #) or user ID

#### Optional Fields
- `username` (string): Custom bot username (default: "Hackathon Bot")
- `icon_emoji` (string): Custom emoji icon for the bot

#### Response (Success)
```json
{
  "success": true,
  "message": "Message sent successfully"
}
```

#### Error Responses
```json
{
  "success": false,
  "error": "Missing request body"
}
```
**Status Code**: 400

```json
{
  "success": false,
  "error": "Message is required"
}
```
**Status Code**: 400

```json
{
  "success": false,
  "error": "Channel is required"
}
```
**Status Code**: 400

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
  "error": "Failed to send message"
}
```
**Status Code**: 500

## Features

1. **User Activity Tracking**: Monitor active Slack users
2. **Presence Information**: Get real-time user presence status
3. **User Profiles**: Access detailed user information
4. **Cache Management**: Admin tools for cache control
5. **Message Sending**: Send messages as bot to channels/users
6. **Flexible Filtering**: Filter users by activity and presence

## Notes for Frontend Development

1. **Authentication**: All endpoints require user authentication
2. **Admin Permissions**: Cache and messaging endpoints require `volunteer.admin` permission
3. **Organization Context**: Admin operations require `X-Org-Id` header
4. **Content-Type**: POST requests should use `application/json`
5. **Response Format**: All responses include `success` field
6. **User IDs**: Slack user IDs are in format U123456789
7. **Channel Format**: Use # prefix for channel names, or direct user IDs
8. **Rate Limiting**: Slack API calls may be rate-limited
9. **Presence Filtering**: Use minimum_presence to filter by user availability
10. **Error Handling**: Check both `success` field and HTTP status codes
11. **Bot Configuration**: Customize bot appearance with username and emoji
12. **Time Ranges**: Activity lookback period configurable from 1-365 days