# Volunteers API Documentation

## Base URL
`/api`

## Authentication
Most endpoints support optional authentication. Admin endpoints require specific permissions.

## Key Volunteer Types
- **mentor**: Mentors who help participants
- **sponsor**: Event sponsors  
- **judge**: Hackathon judges
- **volunteer**: General volunteers
- **hacker**: Hackathon participants

## Application Endpoints

### Submit/Update Applications
**POST** `/api/{volunteer_type}/application/{event_id}/submit`
**POST** `/api/{volunteer_type}/application/{event_id}/update`

Submit or update volunteer applications for specific events.

#### Path Parameters
- `volunteer_type`: mentor | sponsor | judge | volunteer | hacker
- `event_id`: Event ID

#### Authentication
- Optional user authentication

#### Request Body (JSON)
```json
{
  "email": "user@example.com",
  "firstName": "John",
  "lastName": "Doe",
  "company": "ABC Corp",
  "experience": "5 years",
  "skills": ["JavaScript", "Python"],
  "availability": ["Saturday 9-12", "Sunday 1-5"],
  "recaptchaToken": "token_here"
}
```

#### Required Fields
- `email`: Valid email address

#### Response (Success)
```json
{
  "success": true,
  "message": "Application submitted successfully",
  "data": {
    "id": "volunteer_id",
    "email": "user@example.com",
    "type": "mentor",
    "status": "pending"
  }
}
```

---

### Get Applications  
**GET** `/api/{volunteer_type}/application/{event_id}`

Get volunteer application for specific event.

#### Query Parameters
- `userId` (optional): Get application for specific user ID

#### Authentication
- Optional user authentication
- Can use userId parameter for non-authenticated requests

#### Response (Success)
```json
{
  "success": true,
  "message": "Application retrieved successfully",
  "data": {
    "id": "volunteer_id",
    "email": "user@example.com",
    "firstName": "John",
    "status": "approved",
    "skills": ["JavaScript"],
    "availability": ["Saturday 9-12"]
  }
}
```

---

### Admin List Applications
**GET** `/api/admin/{volunteer_type}s/{event_id}`

Get paginated list of applications for admin review.

#### Authentication Required
- User must have admin permissions

#### Query Parameters
- `page` (int, default: 1): Page number
- `limit` (int, default: 20): Items per page  
- `selected` (boolean, optional): Filter by selection status

#### Response
```json
{
  "success": true,
  "data": {
    "volunteers": [
      {
        "id": "volunteer_id",
        "email": "user@example.com",
        "firstName": "John",
        "status": "pending",
        "selected": false
      }
    ],
    "page": 1,
    "limit": 20,
    "total": 50
  }
}
```

---

### Update Selection Status (Admin)
**POST** `/api/admin/volunteer/{volunteer_id}/select`

Update volunteer selection status.

#### Authentication Required
- Admin permissions

#### Request Body
```json
{
  "selected": true
}
```

---

## Hacker-Specific Endpoints

### Get Hacker Applications for Team Matching
**GET** `/api/hacker/applications/{event_id}`

Get all hacker applications for team matching (those wanting to be matched).

---

### Get Volunteer Count by Time Slot
**GET** `/api/volunteer/application_count_by_availability_timeslot/{event_id}`

Get count of volunteer applications by availability time slots.

---

## Mentor Check-in System

### Get Check-in Status
**GET** `/api/mentor/checkin/{event_id}/status`

Get current check-in status for authenticated mentor.

#### Response
```json
{
  "success": true,
  "data": {
    "isCheckedIn": true,
    "checkInTime": "2024-01-15T10:30:00Z",
    "timeSlot": "Morning Session"
  }
}
```

### Check In
**POST** `/api/mentor/checkin/{event_id}/in`

Check in a mentor for an event.

#### Request Body (Optional)
```json
{
  "timeSlot": "Morning Session"
}
```

#### Response
```json
{
  "success": true,
  "data": {
    "message": "Checked in successfully",
    "checkInTime": "2024-01-15T10:30:00Z",
    "timeSlot": "Morning Session",
    "slackNotificationSent": true
  }
}
```

### Check Out
**POST** `/api/mentor/checkin/{event_id}/out`

Check out a mentor from an event.

#### Response
```json
{
  "success": true,
  "data": {
    "message": "Checked out successfully", 
    "checkInDuration": "2 hours 30 minutes",
    "slackNotificationSent": true
  }
}
```

---

## Admin Messaging

### Send Message to Volunteer
**POST** `/api/admin/{volunteer_id}/message`

Send message to volunteer via Slack and email.

#### Authentication Required
- Admin permissions with `volunteer.admin`

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body
```json
{
  "message": "Important update about the event",
  "recipient_type": "volunteer",
  "recipient_id": "volunteer_id"
}
```

## Features

1. **Multi-Type Support**: Handle different volunteer types with shared patterns
2. **Flexible Authentication**: Support both authenticated and anonymous applications
3. **Admin Management**: Comprehensive admin tools for application review
4. **Check-in System**: Real-time mentor check-in/out tracking
5. **Team Matching**: Special support for hacker team matching
6. **Notification Integration**: Slack notifications for check-ins and messaging
7. **Time Slot Analytics**: Track volunteer availability patterns

## Notes for Frontend Development  

1. **Volunteer Types**: Use consistent patterns across mentor/sponsor/judge/volunteer/hacker
2. **Optional Auth**: Handle both logged-in and anonymous application flows
3. **reCAPTCHA**: Include reCAPTCHA token for spam prevention
4. **Response Format**: All responses follow `{success, message, data}` pattern
5. **Admin Interface**: Build admin views for application review and selection
6. **Check-in UI**: Create real-time check-in interface for mentors
7. **Time Slots**: Support flexible time slot definitions
8. **Email Validation**: Validate email format on frontend
9. **Error Handling**: Check `success` field in all responses
10. **Messaging**: Build admin messaging interface for volunteer communication