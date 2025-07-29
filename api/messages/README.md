# Messages API Documentation

## Base URL
`/api/messages`

## Authentication
Mixed authentication requirements - some public, others require admin permissions.

## Core Message Endpoints

### Public Message
**GET** `/api/messages/public`
Public endpoint returning basic system information.

### Protected Message  
**GET** `/api/messages/protected`
Requires user authentication.

### Admin Message
**GET** `/api/messages/admin`
Requires authentication and admin permissions.

---

## Nonprofit (NPO) Management

### NPO CRUD Operations
**POST** `/api/messages/npo` - Create NPO (Admin)
**PATCH** `/api/messages/npo` - Update NPO (Admin)  
**DELETE** `/api/messages/npo` - Delete NPO (Admin)
**GET** `/api/messages/npos` - List all NPOs (Public)
**GET** `/api/messages/npo/{npo_id}` - Get single NPO (Public)

### NPO Applications
**POST** `/api/messages/npo/submit-application` - Submit NPO application (Public with reCAPTCHA)
**GET** `/api/messages/npo/applications` - Get applications (Admin)
**PATCH** `/api/messages/npo/applications/{application_id}` - Update application (Admin)

### NPO-Hackathon Relationships
**GET** `/api/messages/npos/hackathon/{id}` - Get NPOs by hackathon
**POST** `/api/messages/hackathon/nonprofit` - Add NPO to hackathon (Admin)
**DELETE** `/api/messages/hackathon/nonprofit` - Remove NPO from hackathon (Admin)

---

## Hackathon Management

### Hackathon CRUD
**POST** `/api/messages/hackathon` - Create hackathon (Admin)
**PATCH** `/api/messages/hackathon` - Update hackathon (Admin)
**GET** `/api/messages/hackathons` - List hackathons (Public)
  - Query param `current`: Get current hackathons
  - Query param `previous`: Get past hackathons
**GET** `/api/messages/hackathon/{event_id}` - Get single hackathon (Public)

### Volunteer Management by Hackathon
**POST** `/api/messages/hackathon/{event_id}/{volunteer_type}` - Add single volunteer (Admin)
**GET** `/api/messages/hackathon/{event_id}/{volunteer_type}` - Get volunteers by type (Public)
**PATCH** `/api/messages/hackathon/{event_id}/{volunteer_type}` - Update volunteers (Admin)

Volunteer types: `mentor`, `judge`, `volunteer`, `sponsor`, `hacker`

---

## Team Management

### Team Operations
**GET** `/api/messages/teams` - Get all teams (Public)
**GET** `/api/messages/team/{team_id}` - Get single team (Public)
**POST** `/api/messages/teams/batch` - Get multiple teams by IDs (Public)
**GET** `/api/messages/team/{event_id}` - Get teams by event (Admin)
**POST** `/api/messages/team` - Create team (Authenticated)
**DELETE** `/api/messages/team` - Leave team (Authenticated)
**PATCH** `/api/messages/team` - Join team (Authenticated)

---

## Content Management

### News Management
**POST** `/api/messages/news` - Create news (API Key required)
**GET** `/api/messages/news` - Get news list (Public)
  - Query param `limit`: Limit number of results
**GET** `/api/messages/news/{id}` - Get single news item (Public)

### Lead Management
**POST** `/api/messages/lead` - Submit lead (Public, async)

### Praise System
**POST** `/api/messages/praise` - Submit praise (API Key required)
**GET** `/api/messages/praises` - Get all praises (Public)
**GET** `/api/messages/praise/{user_id}` - Get praises for user (Public)

---

## User Management

### Profile Management (Legacy)
**GET** `/api/messages/profile` - Get user profile (Authenticated)
**POST** `/api/messages/profile` - Save user profile (Authenticated)
**GET** `/api/messages/profile/{id}` - Get profile by ID (Public)
**GET** `/api/messages/admin/profiles` - Get all profiles (Admin)

### GitHub Integration
**GET** `/api/messages/profile/github/{username}` - Get GitHub profile (Public)
**GET** `/api/messages/github-repos/{event_id}` - Get GitHub repos for event (Public)

### Helping Status
**POST** `/api/messages/profile/helping` - Register helping status (Authenticated)

---

## Feedback & Surveys

### User Feedback
**POST** `/api/messages/feedback` - Submit feedback (Authenticated)
**GET** `/api/messages/feedback` - Get user's feedback (Authenticated)

### Giveaway System
**POST** `/api/messages/giveaway` - Submit giveaway entry (Authenticated)
**GET** `/api/messages/giveaway` - Get user's giveaway entries (Authenticated)
**GET** `/api/messages/giveaway/admin` - Get all giveaways (Admin)

### Onboarding Feedback
**POST** `/api/messages/onboarding_feedback` - Submit onboarding feedback (Public)

---

## Hackathon Creation Workflow

### Hackathon Requests
**POST** `/api/messages/create-hackathon` - Submit hackathon creation request (Public)
**GET** `/api/messages/create-hackathon/{request_id}` - Get hackathon request (Public)
**PATCH** `/api/messages/create-hackathon/{request_id}` - Update hackathon request (Public)

---

## File Management

### Image Upload
**POST** `/api/messages/upload-image` - Upload image to CDN (Authenticated)
Accepts binary data, base64, or standard image formats.

---

## Legacy Problem Statements
**POST** `/api/messages/problem_statement` - Create problem statement (Admin)
**GET** `/api/messages/problem_statements` - Get problem statements (Public)  
**GET** `/api/messages/problem_statement/{project_id}` - Get single problem statement (Public)

## Key Features

1. **Mixed Authentication**: Public, authenticated, and admin-only endpoints
2. **API Key Protection**: Some endpoints require API keys (news, praise)
3. **reCAPTCHA Integration**: Spam protection for public forms
4. **File Upload**: CDN integration for image uploads
5. **Legacy Support**: Backwards compatibility with older endpoints
6. **Async Operations**: Support for async lead processing
7. **Rich Relationships**: Complex relationships between NPOs, hackathons, teams

## Notes for Frontend Development

1. **Authentication Levels**: Handle public, authenticated, and admin flows
2. **API Keys**: Some operations require backend API keys (not frontend)
3. **Content-Type**: Most POST/PATCH endpoints expect `application/json`
4. **Error Handling**: Check for various error response formats
5. **Legacy Endpoints**: Some endpoints marked for future replacement
6. **File Uploads**: Special handling for image upload endpoint
7. **Query Parameters**: Many GET endpoints support filtering via query params
8. **Async Operations**: Handle async responses appropriately
9. **Organization Context**: Admin operations may require `X-Org-Id` header
10. **Rate Limiting**: Be aware of potential rate limits on external integrations