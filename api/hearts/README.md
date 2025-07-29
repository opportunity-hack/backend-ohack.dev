# Hearts API Documentation

## Base URL
`/api`

## Authentication
All endpoints require user authentication.

## Endpoints

### Get Hearts
**GET** `/api/hearts`

Get hearts data for all users in the system.

#### Authentication Required
- User must be authenticated

#### Response
```json
{
  "hearts": [
    {
      "user_id": "user1",
      "hearts_count": 15,
      "hearts_given": 8,
      "hearts_received": 7,
      "username": "john_doe"
    },
    {
      "user_id": "user2", 
      "hearts_count": 12,
      "hearts_given": 5,
      "hearts_received": 7,
      "username": "jane_smith"
    }
  ]
}
```

#### Error Response
```text
"Error: Could not obtain user details for GET /hearts"
```

---

### Save Hearts (Admin)
**POST** `/api/hearts`

Save or update hearts data (admin only).

#### Authentication Required
- User must be authenticated
- User must be org member with `heart.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "recipient_user_id": "user123",
  "hearts_to_give": 3,
  "reason": "Great mentoring during the hackathon",
  "category": "mentoring"
}
```

#### Response (Success)
```json
{
  "hearts": {
    "success": true,
    "hearts_given": 3,
    "recipient": "user123",
    "total_hearts": 18
  }
}
```

#### Error Response
```text
"Error: Could not obtain user details for POST /hearts"
```

## Features

1. **Hearts System**: Tracks appreciation/recognition between users
2. **Admin Controls**: Only admin users can give hearts
3. **User Visibility**: All authenticated users can view hearts leaderboard
4. **Categorization**: Hearts can be categorized by type (mentoring, helping, etc.)
5. **Audit Trail**: System tracks who gives hearts and reasons

## Notes for Frontend Development

1. **Authentication**: All endpoints require PropelAuth authentication
2. **Admin Permissions**: Only users with `heart.admin` permission can save hearts
3. **Organization Context**: Admin operations require `X-Org-Id` header
4. **Content-Type**: POST requests should use `application/json`
5. **Error Format**: Errors return string messages, not JSON objects
6. **Hearts Display**: Use the GET endpoint to show hearts leaderboard
7. **Admin Interface**: Build admin interface for giving hearts with reason/category
8. **Real-time Updates**: Consider refreshing hearts data after admin actions
9. **User Recognition**: Hearts system is for recognizing user contributions