# Newsletter Subscription API Documentation

## Base URL
`/api/newsletter-subs`

## Authentication
All endpoints require authentication and admin permissions.

## Endpoints

### Test Endpoint
**GET** `/api/newsletter-subs/`

Test endpoint for newsletter subscription functionality.

#### Authentication Required
- User must be authenticated
- User must have `admin_messages_permissions.read` permission

#### Response
```json
{
  "true": "false"
}
```

---

### Subscribe to Newsletter
**POST** `/api/newsletter-subs/subscribe/{doc_id}`

Add user to newsletter subscription list.

#### Path Parameters
- `doc_id` (string): User document ID

#### Authentication Required
- User must be authenticated

#### Response
Returns result from newsletter service (structure depends on service implementation).

---

### Verify Subscription
**POST** `/api/newsletter-subs/verify/{doc_id}`

Check if user is subscribed to newsletter.

#### Path Parameters
- `doc_id` (string): User document ID

#### Authentication Required
- User must be authenticated

#### Response
Returns subscription verification result from newsletter service.

---

### Unsubscribe from Newsletter
**POST** `/api/newsletter-subs/unsubscribe/{doc_id}`

Remove user from newsletter subscription list.

#### Path Parameters
- `doc_id` (string): User document ID

#### Authentication Required
- User must be authenticated

#### Response
Returns result from newsletter service (structure depends on service implementation).

## Notes for Frontend Development

1. **Authentication**: All endpoints require user authentication
2. **Permissions**: The test endpoint requires admin permissions
3. **User ID**: The `doc_id` parameter represents the user's document ID in the system
4. **Content-Type**: All POST requests should use `application/json`
5. **Error Handling**: Check authentication status before making requests
6. **Admin Access**: Only admin users can access the test endpoint
7. **Service Dependencies**: Responses depend on the underlying newsletter service implementation