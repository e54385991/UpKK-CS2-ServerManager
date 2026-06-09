# Fix: GitHub Token Visibility After Page Refresh

## Issue
User reported: "保存后刷新页面 看不到 GitHub 个人访问令牌 ?"
(Translation: "After saving and refreshing the page, can't see the GitHub personal access token?")

## Root Cause
The GitHub token field was intentionally cleared after successful save for security reasons (password input type), but there was no visual indicator to show that a token was actually configured. This caused user confusion as they couldn't verify their token was saved.

## Solution Implemented (Commit: 031364f)

### Backend Changes

1. **New Schema** (`modules/schemas.py`):
```python
class GitHubTokenStatusResponse(SQLModel):
    """Schema for GitHub token status response"""
    has_token: bool
    token_prefix: Optional[str] = None  # Shows first 20 chars without revealing full token
```

2. **New API Endpoint** (`api/routes/auth.py`):
```python
@router.get("/github-token-status", response_model=GitHubTokenStatusResponse)
async def get_github_token_status(current_user: User = Depends(get_current_active_user)):
    """Get GitHub token configuration status (without revealing the full token)"""
    has_token = current_user.has_github_token
    token_prefix = None
    
    if has_token and current_user.github_token:
        token_prefix = current_user.github_token[:20] + "..."
    
    return {"has_token": has_token, "token_prefix": token_prefix}
```

### Frontend Changes (`templates/profile.html`)

1. **Status Indicator HTML**:
```html
<div id="github-token-status" class="d-none mb-2">
    <div class="alert alert-success alert-sm py-2">
        <i class="bi bi-check-circle-fill"></i>
        <strong>Token configured:</strong> 
        <code id="github-token-prefix" class="small">Loading...</code>
    </div>
</div>
```

2. **JavaScript Function**:
```javascript
async function loadGitHubTokenStatus() {
    const response = await fetch('/api/auth/github-token-status', {
        headers: {'Authorization': `Bearer ${token}`}
    });
    
    if (response.ok) {
        const data = await response.json();
        if (data.has_token && data.token_prefix) {
            // Show green indicator with token prefix
            document.getElementById('github-token-prefix').textContent = data.token_prefix;
            document.getElementById('github-token-status').classList.remove('d-none');
        } else {
            // Hide indicator if no token
            document.getElementById('github-token-status').classList.add('d-none');
        }
    }
}
```

3. **Call on Page Load**:
```javascript
// Added to page initialization
loadGitHubTokenStatus();
```

4. **Call After Save**:
```javascript
// Added to profile update success handler
loadGitHubTokenStatus(); // Reload status after saving
```

## User Experience Flow

### Before Fix
1. User saves GitHub token
2. Success message appears, token field is cleared
3. User refreshes page
4. ❌ No indication that token is configured
5. User confused if token was saved

### After Fix
1. User saves GitHub token
2. Success message appears, token field is cleared
3. ✅ Green indicator shows: "✓ Token configured: github_pat_11AEYG54..."
4. User refreshes page
5. ✅ Green indicator persists, confirming token is saved
6. User has confidence token is configured correctly

## Security Considerations

✅ **Full token never exposed**: Only first 20 characters shown
✅ **Password input type**: Token field remains password-protected
✅ **Cleared after save**: Input field empties for security
✅ **Prefix is sufficient**: 20 chars enough to identify token without revealing it
✅ **Authenticated endpoint**: Status only accessible to logged-in user

## Visual Example

```
┌─────────────────────────────────────────────────────────┐
│ GitHub Personal Access Token                            │
│                                                          │
│ ┌───────────────────────────────────────────────────┐   │
│ │ ✓ Token configured: github_pat_11AEYG54Q0jnx... │   │
│ └───────────────────────────────────────────────────┘   │
│                                                          │
│ [                                              ]         │
│ Get your token from GitHub Token Settings →             │
└─────────────────────────────────────────────────────────┘
```

## Files Modified
- `modules/schemas.py` - Added GitHubTokenStatusResponse
- `modules/__init__.py` - Export new schema
- `api/routes/auth.py` - Added /github-token-status endpoint
- `templates/profile.html` - Added status indicator UI and JavaScript

## Testing
- [x] Token status shows when token is configured
- [x] Status hides when no token configured
- [x] Status persists after page refresh
- [x] Status updates after saving new token
- [x] Status hides after removing token (empty save)
- [x] Prefix shows correct first 20 characters
- [x] Full token never exposed in UI or API

## Conclusion
This fix provides users with clear visual confirmation that their GitHub token is configured while maintaining security by not exposing the full token value.
