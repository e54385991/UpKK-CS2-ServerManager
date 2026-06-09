# Test Plan for GitHub Personal Access Token Feature

## Manual Testing Checklist

### 1. Database Migration
- [ ] Run migration script: `mysql -u user -p cs2_manager < db/migrations/add_github_token.sql`
- [ ] Verify `github_token` column exists in users table
- [ ] Verify column is VARCHAR(255) NULL DEFAULT NULL
- [ ] Verify existing users have NULL value for github_token

### 2. Profile Page UI
- [ ] Navigate to Profile page while logged in
- [ ] Verify "GitHub Personal Access Token" field is visible
- [ ] Verify field has appropriate help text with link to GitHub
- [ ] Verify field type is password (for security)
- [ ] Verify placeholder shows "github_pat_... or ghp_..."
- [ ] Verify CAPTCHA is displayed and functional

### 3. Token Validation - Fine-grained Tokens
- [ ] Enter valid fine-grained token (github_pat_XXXXXXXXXXXXXXXX)
- [ ] Complete CAPTCHA and submit
- [ ] Verify success message appears
- [ ] Verify token field is cleared after successful save
- [ ] Verify success message confirms "GitHub token has been securely saved"

### 4. Token Validation - Classic Tokens
Test each classic token prefix:
- [ ] ghp_ prefix - should be accepted
- [ ] gho_ prefix - should be accepted
- [ ] ghu_ prefix - should be accepted
- [ ] ghs_ prefix - should be accepted
- [ ] ghr_ prefix - should be accepted

### 5. Token Validation - Invalid Tokens
Test rejection of invalid formats:
- [ ] Random string without proper prefix - should be rejected
- [ ] Token with spaces - should be rejected
- [ ] Empty string - should be accepted (clears token)
- [ ] Token starting with "git_" - should be rejected

### 6. GitHub API Integration - Public Repository
- [ ] Configure valid GitHub token in profile
- [ ] Navigate to Plugin Market
- [ ] Fetch releases from a public repository
- [ ] Verify releases are fetched successfully
- [ ] Check browser console/network tab for Authorization header

### 7. GitHub API Integration - Private Repository
Prerequisites: Create a private GitHub repo with releases, configure fine-grained token with access

- [ ] Configure fine-grained token with access to private repo
- [ ] Try to fetch releases from private repository
- [ ] Verify releases are accessible
- [ ] Remove token and try again
- [ ] Verify releases are NOT accessible without token

### 8. Rate Limiting Test
- [ ] Without token: Make multiple GitHub API requests
- [ ] Verify rate limit headers show 60 requests/hour
- [ ] With token: Make multiple GitHub API requests  
- [ ] Verify rate limit headers show 5000 requests/hour

### 9. Token Update and Removal
- [ ] Set a token in profile
- [ ] Update profile with a different token
- [ ] Verify new token is used for API requests
- [ ] Update profile with empty token field
- [ ] Verify token is removed from database
- [ ] Verify API requests no longer include Authorization header

### 10. Security Tests
- [ ] Token should not be visible in API responses
- [ ] Token should not appear in browser console logs
- [ ] Token should not be sent in GET request URLs
- [ ] Token should only be in Authorization header for GitHub API requests
- [ ] Verify password input type hides token in UI

### 11. Multi-user Tests
- [ ] User A sets token A
- [ ] User B sets token B
- [ ] Verify User A's requests use token A
- [ ] Verify User B's requests use token B
- [ ] Verify tokens don't leak between users

### 12. Error Handling
- [ ] Invalid CAPTCHA with valid token - should fail
- [ ] Network error during profile update - should show error
- [ ] Invalid token format - should show validation error
- [ ] Expired GitHub token - GitHub API should return 401

## Expected Results Summary

### Success Criteria
✅ Token can be saved and updated successfully
✅ Token is used for all GitHub API requests
✅ Private repositories are accessible with proper token
✅ Rate limits are improved with token (5000 vs 60)
✅ Token validation prevents invalid formats
✅ Token can be removed by submitting empty field
✅ No security vulnerabilities (token not exposed)
✅ No cross-user token leakage

### Performance Expectations
- Profile update: < 2 seconds
- GitHub API request with token: Same as without token
- Token validation: Instant (client-side)

## Test Environment

### Required Setup
- CS2 Server Manager running instance
- MySQL database with migration applied
- GitHub account with:
  - Public repository with releases
  - Private repository with releases
  - Fine-grained personal access token configured
  - Classic personal access token (for testing)

### Test Data
Example valid tokens (use your own):
- Fine-grained: `github_pat_11AAAAAAA0AaAaAaAaAaAa_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345678901234567890`
- Classic: `ghp_abcdefghijklmnopqrstuvwxyz123456`

## Regression Testing
After all tests pass, verify these existing features still work:
- [ ] Login/logout functionality
- [ ] Profile update without GitHub token
- [ ] Steam API key management
- [ ] Plugin installation from public repos
- [ ] Plugin market browsing

## Notes
- For security, clear tokens from test environment after testing
- Rotate any tokens used in testing
- Document any issues found during testing
