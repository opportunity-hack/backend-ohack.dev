# Validation API Documentation

## Base URL
`/api`

## Endpoints

### Validate Slack Channel
**GET** `/api/validate/slack/{channel}`

Validate if a Slack channel name is valid and exists.

#### Path Parameters
- `channel` (string): Slack channel name to validate (without # prefix)

#### Response (Valid Channel)
```json
{
  "valid": true,
  "message": "Channel is valid"
}
```
**Status Code**: 200

#### Response (Invalid Channel)
```json
{
  "valid": false,
  "message": "Channel does not exist or is invalid"
}
```
**Status Code**: 400

#### Response (Validation Error)
```json
{
  "valid": false,
  "message": "Error validating channel: specific error details"
}
```
**Status Code**: 500

---

### Validate GitHub User
**GET** `/api/validate/github/{username}`

Validate if a GitHub username exists.

#### Path Parameters
- `username` (string): GitHub username to validate

#### Response (Valid User)
```json
{
  "valid": true,
  "message": "GitHub user exists",
  "user_data": {
    "username": "github_username",
    "profile_info": "additional_profile_data"
  }
}
```
**Status Code**: 200

#### Response (User Not Found)
```json
{
  "valid": false,
  "message": "GitHub user does not exist"
}
```
**Status Code**: 404

#### Response (Validation Error)
```json
{
  "valid": false,
  "message": "Error validating GitHub user: specific error details"
}
```
**Status Code**: 500

## Features

1. **Slack Integration**: Validates channels against Slack API
2. **GitHub Integration**: Validates usernames against GitHub API
3. **Real-time Validation**: Checks current status of channels/users
4. **Error Differentiation**: Different status codes for different error types

## Notes for Frontend Development

1. **No Authentication**: These endpoints do not require user authentication
2. **Channel Names**: Slack channel names should not include the # prefix
3. **Username Format**: GitHub usernames should be provided as-is
4. **Status Codes**: Use HTTP status codes to differentiate between validation results:
   - 200: Valid/exists
   - 400: Invalid format or doesn't exist
   - 404: Specifically for GitHub users that don't exist
   - 500: Service/API errors
5. **Error Handling**: Check both `valid` field and HTTP status code
6. **Rate Limiting**: These endpoints may be subject to GitHub/Slack API rate limits
7. **Caching**: Consider caching validation results to reduce API calls
8. **Real-time**: Validation reflects current status and may change over time