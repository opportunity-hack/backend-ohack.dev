# Leaderboard API Documentation

## Base URL
`/api/leaderboard`

## Endpoints

### Get GitHub Leaderboard by Event
**GET** `/api/leaderboard/{event_id}`

Get GitHub activity leaderboard data for a specific hackathon event.

#### Path Parameters
- `event_id` (string): The hackathon event ID

#### Response (Success)
```json
{
  "organizations": [
    {
      "name": "org_name",
      "repositories": ["repo1", "repo2"],
      "total_commits": 150,
      "contributors": ["user1", "user2"]
    }
  ],
  "repositories": [
    {
      "name": "repo_name",
      "organization": "org_name", 
      "commits": 75,
      "contributors": ["user1", "user2"]
    }
  ],
  "contributors": [
    {
      "username": "user1",
      "total_commits": 50,
      "repositories": ["repo1", "repo2"]
    }
  ]
}
```

#### Error Response
```json
{
  "error": "Error message describing what went wrong"
}
```
**Status Code**: 500

## Features

1. **GitHub Integration**: Tracks GitHub activity across organizations and repositories
2. **Event-Specific**: Data is filtered by hackathon event ID
3. **Multi-Level Tracking**: Tracks organizations, repositories, and individual contributors
4. **Commit Counting**: Aggregates commit counts at various levels

## Notes for Frontend Development

1. **Event ID**: Must provide a valid hackathon event ID in the URL path
2. **No Authentication**: This endpoint does not require user authentication
3. **GitHub Data**: Data comes from GitHub API integration
4. **Error Handling**: Check for `error` field in response for failed requests
5. **Data Structure**: Response contains three main arrays: organizations, repositories, and contributors
6. **Real-time Data**: Leaderboard reflects current GitHub activity for the event
7. **Sorting**: Frontend should sort data as needed (by commits, contributors, etc.)
8. **Performance**: Consider caching responses as GitHub API calls may be rate-limited