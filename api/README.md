# Exception Handling API Documentation

## Error Handlers

This module provides centralized error handling for API endpoints.

### Internal Server Error Handler
Handles `500 Internal Server Error` responses for API routes.

**Scope**: All routes starting with `/api/`

#### Response (API routes)
```json
{
  "message": "error_description"
}
```
**Status Code**: 500

#### Response (Non-API routes)
Returns the original exception object.

---

### Not Found Error Handler
Handles `404 Not Found` responses for API routes.

**Scope**: All routes starting with `/api/`

#### Response (API routes)
```json
{
  "message": "Not Found"
}
```
**Status Code**: 404

#### Response (Non-API routes)
Returns the original exception object.

## Notes for Frontend Development

1. **Error Format**: All API errors return JSON objects with a `message` field
2. **Status Codes**: Standard HTTP status codes are used (404, 500)
3. **Route Detection**: Only routes starting with `/api/` receive JSON error responses
4. **Non-API Routes**: Web pages and other routes receive standard HTML error pages
5. **Error Handling**: Frontend should check for JSON responses with `message` field to handle errors consistently