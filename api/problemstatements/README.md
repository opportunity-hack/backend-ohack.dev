# Problem Statements API Documentation

## Base URL
`/api/problem-statements`

## Authentication
Admin endpoints require authentication and specific permissions.

## Endpoints

### Create Problem Statement (Admin)
**POST** `/api/problem-statements`

Create a new problem statement.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission  

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "title": "Problem Statement Title",
  "description": "Detailed description of the problem",
  "difficulty": "medium",
  "category": "education",
  "requirements": ["requirement1", "requirement2"],
  "deliverables": ["deliverable1", "deliverable2"]
}
```

#### Response (Success)
```json
{
  "id": "problem_statement_id",
  "title": "Problem Statement Title", 
  "description": "Detailed description of the problem",
  "difficulty": "medium",
  "category": "education",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```
**Status Code**: 201

#### Error Responses
```json
{
  "error": "Validation error message"
}
```
**Status Code**: 400

```json
{
  "error": "Internal server error"
}
```
**Status Code**: 500

---

### Get All Problem Statements
**GET** `/api/problem-statements`

Get list of all problem statements (public endpoint).

#### Response
```json
{
  "problem_statements": [
    {
      "id": "problem_statement_id",
      "title": "Problem Statement Title",
      "description": "Description",
      "difficulty": "medium",
      "category": "education",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

#### Error Response
```json
{
  "error": "Internal server error"
}
```
**Status Code**: 500

---

### Get Single Problem Statement
**GET** `/api/problem-statements/{id}`

Get detailed information about a specific problem statement.

#### Path Parameters
- `id` (string): Problem statement ID

#### Response (Success)
```json
{
  "id": "problem_statement_id",
  "title": "Problem Statement Title",
  "description": "Detailed description",
  "difficulty": "medium", 
  "category": "education",
  "requirements": ["requirement1", "requirement2"],
  "deliverables": ["deliverable1", "deliverable2"],
  "events": [
    {
      "id": "event_id",
      "name": "Hackathon Name",
      "date": "2024-02-15"
    }
  ],
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

#### Error Responses
```json
{
  "error": "Problem statement not found"
}
```
**Status Code**: 404

```json
{
  "error": "Internal server error"
}
```
**Status Code**: 500

---

### Link Problem Statement to Events (Admin)
**PATCH** `/api/problem-statements/events`

Link problem statements to hackathon events.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Headers Required
- `X-Org-Id`: Organization ID

#### Request Body (JSON)
```json
{
  "problem_statement_id": "problem_statement_id",
  "event_ids": ["event_id_1", "event_id_2"]
}
```

#### Response (Success)
```json
{
  "id": "problem_statement_id",
  "title": "Problem Statement Title",
  "events": [
    {
      "id": "event_id_1",
      "name": "Hackathon 1"
    },
    {
      "id": "event_id_2", 
      "name": "Hackathon 2"
    }
  ]
}
```
**Status Code**: 200

#### Error Response
```json
{
  "error": "Problem statement not found"
}
```
**Status Code**: 404

## Features

1. **CRUD Operations**: Create and read problem statements
2. **Event Linking**: Associate problem statements with hackathon events
3. **Categorization**: Support for difficulty levels and categories
4. **Rich Content**: Support for requirements and deliverables
5. **Public Access**: Problem statements list is publicly accessible

## Notes for Frontend Development

1. **Mixed Authentication**: Some endpoints public, others require admin access
2. **Admin Permissions**: Admin endpoints require `volunteer.admin` permission
3. **Organization Context**: Admin operations require `X-Org-Id` header
4. **Content-Type**: POST/PATCH requests should use `application/json`
5. **Error Handling**: Check for `error` field in responses
6. **Event Relationships**: Problem statements can be linked to multiple events
7. **Rich Data**: Response includes created/updated timestamps
8. **Categories**: Support for problem categorization and difficulty levels
9. **Public Listing**: Use GET endpoints for public problem statement browsing
10. **Admin Interface**: Build admin interface for creating and linking problem statements