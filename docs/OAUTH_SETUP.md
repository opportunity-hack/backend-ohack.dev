# OAuth Social Login Setup Guide

This guide explains how to configure social login providers (Slack, Google, etc.) for the Opportunity Hack platform using PropelAuth.

## Overview

The Opportunity Hack platform uses [PropelAuth](https://www.propelauth.com/) as its authentication provider. PropelAuth handles all OAuth flows with social login providers like Slack and Google. The backend Flask application receives authenticated tokens from PropelAuth and validates them.

## Architecture

```
User → PropelAuth (OAuth Flow) → Backend API
                ↓
        Social Provider (Slack/Google/etc.)
```

1. **Frontend**: Users click "Sign in with Google" or "Sign in with Slack"
2. **PropelAuth**: Handles the OAuth flow with the social provider
3. **Backend**: Receives and validates the authentication token from PropelAuth

## Current Configuration

### Slack Login
- **Status**: ✅ Already configured
- **User ID Format**: `oauth2|slack|T1Q7936BH-{SLACK_USER_ID}`
- **Workspace ID**: `T1Q7936BH` (configured in environment)

### Google Login
- **Status**: 🔄 Ready to enable (backend supports it)
- **User ID Format**: `oauth2|google-oauth2|{GOOGLE_USER_ID}`
- **Notes**: No backend code changes needed, only PropelAuth dashboard configuration

## How to Enable Google OAuth in PropelAuth

### Step 1: Access PropelAuth Dashboard

1. Log in to your PropelAuth dashboard at https://auth.propelauth.com
2. Select your project (Opportunity Hack)
3. Navigate to **Authentication** → **Social Logins**

### Step 2: Configure Google OAuth

#### Option A: Using Google Cloud Console

1. **Create Google OAuth Credentials**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Navigate to **APIs & Services** → **Credentials**
   - Click **Create Credentials** → **OAuth 2.0 Client ID**
   
2. **Configure OAuth Consent Screen**:
   - User type: External (for public access)
   - App name: Opportunity Hack
   - User support email: your-email@opportunityhack.org
   - Developer contact: your-email@opportunityhack.org
   - Add scopes: `email`, `profile`, `openid`

3. **Create OAuth Client**:
   - Application type: Web application
   - Name: Opportunity Hack - PropelAuth
   - Authorized redirect URIs: Use the callback URL provided by PropelAuth
     - Format: `https://{YOUR_PROPEL_DOMAIN}/api/backend/v1/oauth/callback/google`
     - Example: `https://123456.propelauthtest.com/api/backend/v1/oauth/callback/google`

4. **Copy Credentials**:
   - Save the **Client ID** and **Client Secret**

#### Option B: Using PropelAuth's Managed Google OAuth (Recommended)

PropelAuth offers a managed Google OAuth option that simplifies setup:

1. In PropelAuth Dashboard → Social Logins
2. Find **Google** in the list of providers
3. Click **Enable**
4. Choose **Use PropelAuth's Google OAuth App** (recommended for quick setup)
5. Configure the settings:
   - ✅ Enable "Sign up with Google"
   - ✅ Enable "Link existing accounts"
   - ✅ Auto-verify email addresses from Google

### Step 3: Configure in PropelAuth Dashboard

1. In PropelAuth Dashboard → **Social Logins** → **Google**
2. Enter your Google OAuth credentials:
   - **Client ID**: (from Google Cloud Console)
   - **Client Secret**: (from Google Cloud Console)
3. Configure additional settings:
   - **Auto-create users**: ✅ Enabled (allow new users to sign up)
   - **Account linking**: ✅ Enabled (allow users to link Google to existing accounts)
   - **Email verification**: ✅ Skip verification for Google (Google already verifies emails)

### Step 4: Test the Integration

1. **Test in PropelAuth Dashboard**:
   - Use PropelAuth's built-in testing tools
   - Navigate to **Users** → **Create Test User**
   - Try signing in with Google

2. **Test with Frontend**:
   - Deploy the frontend with PropelAuth's updated configuration
   - Click "Sign in with Google"
   - Complete the OAuth flow
   - Verify successful authentication

3. **Verify Backend**:
   - Check logs for successful authentication
   - Verify user ID format: `oauth2|google-oauth2|{GOOGLE_USER_ID}`
   - Test API calls with Google-authenticated users

## Backend Code Support

The backend has been updated to support multiple OAuth providers:

### User ID Formats Supported

```python
# Slack
oauth2|slack|T1Q7936BH-U12345ABC

# Google  
oauth2|google-oauth2|1234567890123456789

# Future providers (GitHub, Microsoft, etc.)
oauth2|github|12345678
oauth2|microsoft|uuid-here
```

### Utility Functions

The backend includes utility functions in `common/utils/oauth_providers.py`:

- `get_oauth_provider_from_user_id(user_id)`: Extract provider name
- `is_slack_user_id(user_id)`: Check if user ID is from Slack
- `is_google_user_id(user_id)`: Check if user ID is from Google
- `get_provider_display_name(user_id)`: Get human-readable provider name

### No Code Changes Needed

Once Google OAuth is enabled in PropelAuth:
- ✅ Backend automatically accepts Google OAuth tokens
- ✅ User IDs are stored with the `oauth2|google-oauth2|` prefix
- ✅ All existing API endpoints work with Google-authenticated users
- ✅ Authorization and permissions work the same way

## Environment Variables

The backend requires these PropelAuth environment variables (already configured):

```bash
# PropelAuth Settings
PROPEL_AUTH_URL=https://123456.propelauthtest.com
PROPEL_AUTH_KEY=your-api-key-here

# Optional: Slack workspace ID (for backward compatibility)
SLACK_WORKSPACE_ID=T1Q7936BH
```

No additional environment variables are needed for Google OAuth.

## Frontend Integration

The frontend needs to be updated to show the "Sign in with Google" button. This is typically done in the PropelAuth component:

```javascript
// Example frontend code (React)
import { useAuth } from '@propelauth/react'

function LoginPage() {
  const { redirectToLoginPage } = useAuth()
  
  return (
    <div>
      <button onClick={() => redirectToLoginPage()}>
        Sign in with Google
      </button>
      <button onClick={() => redirectToLoginPage()}>
        Sign in with Slack
      </button>
    </div>
  )
}
```

PropelAuth automatically shows all enabled social login options.

## Security Considerations

1. **OAuth Scopes**: Only request necessary scopes (`email`, `profile`)
2. **Email Verification**: Google-authenticated emails are pre-verified
3. **Account Linking**: Users can link multiple OAuth providers to one account
4. **Session Management**: Handled by PropelAuth with secure tokens

## Troubleshooting

### Issue: "Redirect URI mismatch"
**Solution**: Ensure the redirect URI in Google Cloud Console exactly matches PropelAuth's callback URL

### Issue: "Access denied" from Google
**Solution**: Check OAuth consent screen configuration and approved scopes

### Issue: User can't sign in with Google
**Solution**: 
- Verify Google OAuth is enabled in PropelAuth dashboard
- Check that the frontend is using the latest PropelAuth configuration
- Review PropelAuth logs for error details

### Issue: User ID format issues
**Solution**: The backend automatically handles different OAuth provider formats. No action needed.

## Testing Checklist

- [ ] Enable Google OAuth in PropelAuth dashboard
- [ ] Configure Google OAuth credentials
- [ ] Test sign-in flow in PropelAuth dashboard
- [ ] Update frontend to show Google login option
- [ ] Test new user sign-up with Google
- [ ] Test existing user sign-in with Google
- [ ] Test account linking (user signs in with both Slack and Google)
- [ ] Verify user profile data is correctly synced
- [ ] Test API authentication with Google-authenticated users
- [ ] Check database for correct user ID format

## Support

For issues with:
- **PropelAuth Configuration**: Contact PropelAuth support or check their [documentation](https://docs.propelauth.com/)
- **Google OAuth Setup**: Refer to [Google OAuth Documentation](https://developers.google.com/identity/protocols/oauth2)
- **Backend Integration**: Check this repository's issues or contact the development team

## Additional Resources

- [PropelAuth Documentation](https://docs.propelauth.com/)
- [PropelAuth Social Login Guide](https://docs.propelauth.com/overview/social-login)
- [Google OAuth 2.0 Documentation](https://developers.google.com/identity/protocols/oauth2)
- [OAuth 2.0 Best Practices](https://tools.ietf.org/html/draft-ietf-oauth-security-topics)
