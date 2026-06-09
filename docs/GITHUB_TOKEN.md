# GitHub Personal Access Token Support

## Overview

This feature allows users to configure their GitHub Fine-grained personal access tokens in their profile settings. The token is automatically used when accessing GitHub APIs for installing plugins, accessing private repositories, and getting better API rate limits.

## Benefits

1. **Access Private Repositories**: Users can install plugins from their private GitHub repositories
2. **Better Rate Limits**: Authenticated requests get higher rate limits (5,000 requests/hour vs 60 requests/hour for unauthenticated)
3. **Security**: Fine-grained tokens can be scoped to specific repositories and permissions

## How to Use

### 1. Create a GitHub Personal Access Token

1. Go to [GitHub Token Settings](https://github.com/settings/tokens?type=beta)
2. Click "Generate new token" → "Fine-grained token" (recommended)
3. Configure the token:
   - **Token name**: e.g., "CS2 Server Manager"
   - **Expiration**: Choose your preferred expiration (90 days recommended)
   - **Repository access**: 
     - For private repos: Select "Only select repositories" and choose the repos
     - For public repos: Select "Public Repositories (read-only)"
   - **Permissions**: 
     - Contents: Read-only (required)
     - Metadata: Read-only (automatically included)
4. Click "Generate token"
5. Copy the token (it starts with `github_pat_` followed by a long string like `github_pat_11AEYG54Q0jnxLJObXbKbX_...`)

### 2. Add Token to Profile

1. Log in to CS2 Server Manager
2. Navigate to Personal Center (Profile)
3. Find the "GitHub Personal Access Token" field
4. Paste your token
5. Enter CAPTCHA code
6. Click "Update Profile"

### 3. Using the Token

The token is automatically used when:
- Browsing GitHub plugin releases
- Installing plugins from GitHub
- Accessing repository information in the plugin market
- Any other GitHub API operations

## Token Types Supported

- **Fine-grained tokens** (recommended): Start with `github_pat_`
  - Better security with granular permissions
  - Can be scoped to specific repositories
  
- **Classic tokens**: Start with `ghp_`, `gho_`, `ghu_`, `ghs_`, or `ghr_`
  - Broader access but less secure
  - Not recommended for production use

## Security Considerations

1. **Token Storage**: Tokens are stored encrypted in the database
2. **Token Validation**: The system validates token format before saving
3. **Token Scope**: Use the minimum required permissions (read-only for contents)
4. **Token Rotation**: Regularly rotate your tokens (every 90 days recommended)
5. **Token Removal**: Leave the field blank and update to remove the token

## Technical Details

### Database Schema

```sql
ALTER TABLE `users` 
ADD COLUMN `github_token` VARCHAR(255) NULL DEFAULT NULL 
COMMENT 'GitHub Fine-grained personal access token for API authentication';
```

### API Changes

- **Updated `UserProfileUpdate` schema** to accept `github_token`
- **Updated `User` model** with `github_token` field and `has_github_token` property
- **Modified `http_helper`** to automatically add GitHub authentication headers when token is available
- **Updated GitHub API calls** in `github_plugins.py` and `plugin_market.py` to use user's token

### Rate Limits

- **Without token**: 60 requests/hour per IP
- **With token**: 5,000 requests/hour per user
- **For GitHub Enterprise**: Custom limits based on your setup

## Troubleshooting

### Token not working

1. Verify token is correctly copied (no extra spaces)
2. Check token hasn't expired
3. Ensure token has correct permissions (Contents: Read)
4. Verify repository access is configured correctly

### API rate limit still low

1. Confirm token is saved in profile
2. Check token is valid in GitHub settings
3. Ensure you're logged in when accessing plugins

## Migration Guide

If you're upgrading from a previous version:

1. Run the migration script:
   ```bash
   mysql -u your_user -p cs2_manager < db/migrations/add_github_token.sql
   ```

2. Restart the application

3. Users can now configure their tokens in profile settings
