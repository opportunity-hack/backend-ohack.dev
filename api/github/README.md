# GitHub API Documentation

## Base URL
`/api/github`

## Authentication
Some endpoints require authentication and admin permissions.

## Endpoints

### Get GitHub Organization Data
**GET** `/api/github/organization/{org_name}`

Get comprehensive data about a GitHub organization including repositories and contributors.

#### Path Parameters
- `org_name` (string): GitHub organization name

#### Response (Success)
```json
{
  "organization": {
    "name": "organization_name",
    "description": "Organization description",
    "public_repos": 25,
    "followers": 150
  },
  "repositories": [
    {
      "name": "repo_name",
      "description": "Repository description", 
      "stars": 45,
      "forks": 12,
      "commits": 150,
      "contributors": 8
    }
  ],
  "contributors": [
    {
      "username": "contributor1",
      "contributions": 25,
      "repositories": ["repo1", "repo2"]
    }
  ]
}
```

#### Error Response
```json
{
  "error": "Organization not found or API error message"
}
```
**Status Code**: 404 or 500

---

### Get GitHub Repository Data
**GET** `/api/github/repository`

Get detailed information about a specific repository.

#### Query Parameters
- `repo` (string, required): Repository name
- `org` (string, optional): Organization name (searches all orgs if not provided)

#### Response (Success)
```json
{
  "repository": {
    "name": "repo_name",
    "full_name": "org/repo_name",
    "description": "Repository description",
    "stars": 45,
    "forks": 12,
    "language": "JavaScript",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T15:30:00Z"
  },
  "contributors": [
    {
      "username": "contributor1",
      "contributions": 25,
      "avatar_url": "https://github.com/avatar.jpg"
    }
  ],
  "commits": 150,
  "issues": 5,
  "pull_requests": 8
}
```

#### Error Responses
```json
{
  "error": "repo parameter is required"
}
```
**Status Code**: 400

```json
{
  "error": "Repository not found or API error message"
}
```
**Status Code**: 404 or 500

---

### Get Organization Contributors
**GET** `/api/github/organization/{org_name}/contributors`

Get all contributors for a specific GitHub organization.

#### Path Parameters
- `org_name` (string): GitHub organization name

#### Response (Success)
```json
{
  "org_name": "organization_name",
  "contributors": [
    {
      "username": "contributor1",
      "total_contributions": 45,
      "repositories": ["repo1", "repo2", "repo3"],
      "avatar_url": "https://github.com/avatar1.jpg"
    },
    {
      "username": "contributor2", 
      "total_contributions": 32,
      "repositories": ["repo2", "repo4"],
      "avatar_url": "https://github.com/avatar2.jpg"
    }
  ],
  "total_contributors": 2
}
```

#### Error Response
```json
{
  "error": "API error message"
}
```
**Status Code**: 500

---

### Get Repository Contributors
**GET** `/api/github/repository/contributors`

Get all contributors for a specific repository.

#### Query Parameters
- `repo` (string, required): Repository name
- `org` (string, optional): Organization name

#### Response (Success)
```json
{
  "org_name": "organization_name",
  "repo_name": "repository_name",
  "contributors": [
    {
      "username": "contributor1",
      "contributions": 25,
      "avatar_url": "https://github.com/avatar1.jpg"
    }
  ],
  "total_contributors": 1
}
```

#### Error Responses
```json
{
  "error": "repo parameter is required"
}
```
**Status Code**: 400

```json
{
  "error": "API error message"
}
```
**Status Code**: 500

---

### Create GitHub Issue (Admin)
**POST** `/api/github/create_issue`

Create a new issue in a GitHub repository.

#### Authentication Required
- User must be authenticated
- User must be org member with `volunteer.admin` permission

#### Request Body (JSON)
```json
{
  "repo": "repository_name",
  "org": "organization_name",
  "title": "Issue title",
  "body": "Issue description and details"
}
```

#### Required Fields
- `repo` (string): Repository name
- `title` (string): Issue title

#### Optional Fields
- `org` (string): Organization name
- `body` (string): Issue body/description

#### Response (Success)
```json
{
  "success": true,
  "issue": {
    "id": 123,
    "number": 45,
    "title": "Issue title",
    "body": "Issue description",
    "state": "open",
    "created_at": "2024-01-15T10:30:00Z",
    "html_url": "https://github.com/org/repo/issues/45"
  }
}
```

#### Error Responses
```json
{
  "error": "repo and title parameters are required"
}
```
**Status Code**: 400

```json
{
  "error": "API error message"
}
```
**Status Code**: 500

---

### Get GitHub Issues
**GET** `/api/github/issues`

Get issues from a GitHub repository.

#### Query Parameters
- `repo` (string, required): Repository name
- `org` (string, optional): Organization name
- `state` (string, optional): Issue state ('open', 'closed', 'all', default: 'open')

#### Response (Success)
```json
[
  {
    "id": 123,
    "number": 45,
    "title": "Issue title",
    "body": "Issue description",
    "state": "open",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T11:30:00Z",
    "html_url": "https://github.com/org/repo/issues/45",
    "user": {
      "login": "username",
      "avatar_url": "https://github.com/avatar.jpg"
    },
    "labels": [
      {
        "name": "bug",
        "color": "d73a4a"
      }
    ]
  }
]
```

#### Error Responses
```json
{
  "error": "repo parameter is required"
}
```
**Status Code**: 400

```json
{
  "error": "API error message"
}
```
**Status Code**: 500

## Features

1. **Organization Analytics**: Comprehensive org data and statistics
2. **Repository Management**: Detailed repository information and metrics
3. **Contributor Tracking**: Track contributions across organizations and repositories
4. **Issue Management**: Create and retrieve GitHub issues
5. **Flexible Queries**: Support for organization-specific or cross-organization searches
6. **Admin Controls**: Issue creation requires admin permissions

## Notes for Frontend Development

1. **Mixed Authentication**: Most endpoints are public, issue creation requires admin access
2. **Admin Permissions**: Issue creation requires `volunteer.admin` permission  
3. **Query Parameters**: Many endpoints use query parameters instead of path parameters
4. **Required Fields**: Validate required parameters before making requests
5. **Error Handling**: Check for `error` field in responses
6. **Rate Limiting**: GitHub API calls may be rate-limited
7. **Organization Context**: Some endpoints work across all organizations if org not specified
8. **Issue States**: Support for different issue states (open, closed, all)
9. **Rich Data**: Responses include detailed metadata and URLs
10. **Contributor Analytics**: Use contributor endpoints for team analytics and recognition
11. **Repository Discovery**: Use organization endpoints to discover available repositories
12. **Issue Tracking**: Integrate issue management into hackathon project workflows