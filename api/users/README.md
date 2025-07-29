# Users API Documentation

## Base URL
`/api/users`

## Authentication
All endpoints require user authentication.

## Endpoints

### Get User Profile
**GET** `/api/users/profile`

Get the authenticated user's profile metadata.

#### Authentication Required
- User must be authenticated

#### Response
```json
{
  "user_id": "propel_auth_uuid",
  "profile_field1": "value1",
  "profile_field2": "value2"
}
```

#### Error Response
```json
null
```

---

### Save User Profile
**POST** `/api/users/profile`

Save or update the authenticated user's profile metadata.

#### Authentication Required
- User must be authenticated

#### Request Body (JSON)
```json
{
  "profile_field1": "new_value1",
  "profile_field2": "new_value2"
}
```

#### Response
```json
{
  "user_id": "propel_auth_uuid",
  "profile_field1": "new_value1",
  "profile_field2": "new_value2"
}
```

#### Error Response
```json
null
```

---

### Get Profile by Database ID
**GET** `/api/users/{id}/profile`

Get user profile by database ID (public endpoint).

#### Path Parameters
- `id` (string): User database ID

#### Response
Returns profile dictionary or `null` if not found.

---

### Save Volunteering Time
**POST** `/api/users/volunteering`

Save volunteering time data for the authenticated user.

#### Authentication Required
- User must be authenticated

#### Request Body (JSON)
```json
{
  "hours": 5,
  "activity_type": "mentoring",
  "date": "2024-01-15",
  "description": "Helped participants with coding questions"
}
```

#### Response
```json
{
  "user_id": "propel_auth_uuid",
  "volunteering_data": "updated_data"
}
```

#### Error Response
```json
null
```

---

### Get Volunteering Time
**GET** `/api/users/volunteering`

Get volunteering time data for the authenticated user.

#### Authentication Required
- User must be authenticated

#### Query Parameters
- `startDate` (string, optional): Start date filter (YYYY-MM-DD format)
- `endDate` (string, optional): End date filter (YYYY-MM-DD format)

#### Response
```json
{
  "totalActiveHours": 25.5,
  "totalCommitmentHours": 40,
  "allVolunteering": [
    {
      "date": "2024-01-15",
      "hours": 5,
      "activity_type": "mentoring"
    }
  ]
}
```

#### Error Response
```json
null
```

---

### Get All Volunteering Time (Admin)
**GET** `/api/users/admin/volunteering`

Get all volunteering time data for all users (admin only).

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Query Parameters
- `startDate` (string, optional): Start date filter (YYYY-MM-DD format)
- `endDate` (string, optional): End date filter (YYYY-MM-DD format)

#### Response
```json
{
  "totalActiveHours": 150.5,
  "totalCommitmentHours": 200,
  "volunteerSessions": [
    {
      "user_id": "user1",
      "date": "2024-01-15",
      "hours": 5,
      "activity_type": "mentoring"
    }
  ]
}
```

#### Error Response
```json
null
```

## Notes for Frontend Development

1. **Authentication**: All endpoints require PropelAuth authentication
2. **User ID**: The system uses PropelAuth UUIDs for user identification
3. **Profile Structure**: Profile fields depend on the User model implementation
4. **Date Formats**: Use YYYY-MM-DD format for date parameters
5. **Admin Endpoints**: Admin endpoints require specific permissions and org membership
6. **Headers**: Admin endpoints require `X-Org-Id` header
7. **Content-Type**: All POST requests should use `application/json`
8. **Null Responses**: Failed operations return `null` instead of error objects
9. **Time Tracking**: Volunteering endpoints track both active hours and commitment hours