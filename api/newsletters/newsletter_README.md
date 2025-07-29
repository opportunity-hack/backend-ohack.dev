# Newsletter Management API Documentation

## Base URL
`/api/newsletter`

## Authentication
Most endpoints require authentication and admin permissions.

## Endpoints

### Get Subscription List (Admin)
**GET** `/api/newsletter/`

Get the complete newsletter subscription list.

#### Authentication Required
- User must be authenticated
- User must be org member with `admin_permissions`

#### Response
Returns subscription list from newsletter service.

---

### Check User Subscription
**GET** `/api/newsletter/{user_id}`

Check if a specific user is subscribed to the newsletter.

#### Path Parameters
- `user_id` (string): User ID to check

#### Response
Returns subscription status from newsletter service.

---

### Send Newsletter (Admin)
**POST** `/api/newsletter/send_newsletter`

Send newsletter to specified addresses.

#### Request Body (JSON)
```json
{
  "addresses": [
    {"email": "user1@example.com", "id": "user1"},
    {"email": "user2@example.com", "id": "user2"}
  ],
  "subject": "Newsletter Title",  
  "body": "Newsletter HTML content",
  "role": "subscriber"
}
```

#### Required Fields
- `addresses` (array): List of recipient objects with email and id
- `subject` (string): Email subject line
- `body` (string): HTML email content
- `role` (string): Recipient role

#### Response (Success)
```text
"True"
```

#### Response (Error)
```text
"False"
```

---

### Preview Newsletter
**POST** `/api/newsletter/preview_newsletter`

Preview how newsletter content will look when formatted.

#### Request Body (JSON)
```json
{
  "body": "Newsletter HTML content to preview"
}
```

#### Response
Returns formatted HTML content as it would appear to recipients.

#### Error Response
```text
"Error{specific_error_details}"
```

---

### Newsletter Signup/Management
**POST** `/api/newsletter/{subscribe}/{doc_id}`

Manage newsletter subscription for a user.

#### Path Parameters
- `subscribe` (string): Action to perform - "subscribe", "verify", or "unsubscribe"
- `doc_id` (string): User document ID

#### Authentication Required
- User must be authenticated

#### Actions
- `subscribe`: Add user to subscription list  
- `verify`: Check if user is subscribed (returns boolean)
- `unsubscribe`: Remove user from subscription list

#### Response
- For subscribe/unsubscribe: Returns service response
- For verify: Returns boolean subscription status

#### Error Response
```text
"errors"
```

## Features

1. **Admin Management**: Complete newsletter management for admins
2. **User Self-Service**: Users can manage their own subscriptions
3. **Preview Functionality**: Preview emails before sending
4. **Bulk Sending**: Send to multiple recipients at once
5. **Role-Based**: Support for different recipient roles
6. **Verification**: Check subscription status

## Notes for Frontend Development

1. **Authentication**: Most endpoints require authentication
2. **Admin Permissions**: Admin endpoints require `admin_permissions`
3. **Content-Type**: POST requests should use `application/json`
4. **Email Format**: HTML content supported in newsletter body
5. **Response Types**: Mix of JSON objects, strings, and booleans
6. **Error Handling**: Check for "False" or "errors" string responses
7. **Address Format**: Recipients need both email and id fields
8. **Preview Feature**: Use preview endpoint to test formatting before sending
9. **User Actions**: Support subscribe/unsubscribe/verify actions in UI
10. **Role Support**: Include appropriate role when sending newsletters