# Contact API Documentation

## Base URL
`/api`

## Endpoints

### Submit Contact Form
**POST** `/api/contact`

Submit a contact form with reCAPTCHA validation and rate limiting.

#### Request Body (JSON)
```json
{
  "firstName": "John",
  "lastName": "Doe", 
  "email": "john@example.com",
  "organization": "ABC Corp",
  "inquiryType": "hackathon",
  "message": "I'm interested in participating in the hackathon...",
  "receiveUpdates": true,
  "recaptchaToken": "recaptcha_token_here"
}
```

#### Required Fields
- `firstName` (string): First name
- `lastName` (string): Last name
- `email` (string): Valid email address
- `message` (string): Contact message
- `recaptchaToken` (string): reCAPTCHA verification token

#### Optional Fields
- `organization` (string): Organization name (defaults to empty string)
- `inquiryType` (string): Type of inquiry (defaults to empty string)
- `receiveUpdates` (boolean): Whether to receive updates (defaults to false)

#### Response (Success)
```json
{
  "success": true,
  "message": "Contact form submitted successfully"
}
```
**Status Code**: 201

#### Error Responses

**Missing Required Fields**
```json
{
  "success": false,
  "error": "Missing required fields (firstName, lastName, email, message)"
}
```
**Status Code**: 400

**Missing reCAPTCHA Token**
```json
{
  "success": false,
  "error": "Missing reCAPTCHA token"
}
```
**Status Code**: 400

**Invalid Input**
```json
{
  "success": false,
  "error": "Validation error message"
}
```
**Status Code**: 400

**Service Error**
```json
{
  "success": false,
  "error": "Service-specific error message"
}
```
**Status Code**: 400

**Server Error**
```json
{
  "success": false,
  "error": "An error occurred while processing your request"
}
```
**Status Code**: 500

## Features

1. **Rate Limiting**: Based on client IP address
2. **reCAPTCHA Validation**: Prevents automated submissions
3. **Input Validation**: Validates required fields and email format
4. **Error Logging**: Comprehensive logging for debugging
5. **IP Tracking**: Tracks client IP for rate limiting and security

## Notes for Frontend Development

1. **Content-Type**: Request must use `application/json`
2. **reCAPTCHA**: Must include valid reCAPTCHA token from Google reCAPTCHA
3. **Email Validation**: Backend validates email format
4. **Rate Limiting**: Repeated submissions from same IP may be blocked
5. **Error Handling**: Check `success` field in response to determine if submission succeeded
6. **Required Validation**: Frontend should validate required fields before submission
7. **Response Status**: Use HTTP status codes in addition to success field for proper error handling