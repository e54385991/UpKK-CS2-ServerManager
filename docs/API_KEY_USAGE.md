# User API Key Authentication

## Overview

The CS2 Server Manager now supports API key authentication in addition to JWT tokens. This allows users to control their servers programmatically without exposing their passwords.

## Generating an API Key

### Via Web UI

1. Log in to the CS2 Server Manager
2. Navigate to your profile page (`/profile`)
3. Scroll down to the "API Key Management" section
4. Optionally enter the CAPTCHA code (recommended for web UI)
5. Click "Generate API Key"
6. Copy your API key and store it securely

### Programmatically (without CAPTCHA)

You can also generate an API key programmatically using your JWT token:

```bash
# First, log in to get a JWT token
TOKEN=$(curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password", "captcha_token": "", "captcha_code": ""}' \
  | jq -r '.access_token')

# Then generate an API key (CAPTCHA is optional)
curl -X POST http://localhost:8000/api/auth/api-key/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Using Your API Key

You can use your API key by including it in the `X-API-Key` header of your HTTP requests.

### Example: List Your Servers

```bash
curl -H "X-API-Key: YOUR_API_KEY_HERE" \
  http://localhost:8000/api/servers
```

### Example: Get Server Details

```bash
curl -H "X-API-Key: YOUR_API_KEY_HERE" \
  http://localhost:8000/api/servers/1
```

### Example: Start a Server

```bash
curl -X POST \
  -H "X-API-Key: YOUR_API_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"action": "start"}' \
  http://localhost:8000/api/servers/1/action
```

### Example: Using with Python

```python
import requests

API_KEY = "YOUR_API_KEY_HERE"
BASE_URL = "http://localhost:8000"

headers = {
    "X-API-Key": API_KEY
}

# List servers
response = requests.get(f"{BASE_URL}/api/servers", headers=headers)
servers = response.json()
print(f"Found {len(servers)} servers")

# Start a server
response = requests.post(
    f"{BASE_URL}/api/servers/1/action",
    headers=headers,
    json={"action": "start"}
)
print(response.json())
```

### Example: Using with JavaScript/Node.js

```javascript
const API_KEY = 'YOUR_API_KEY_HERE';
const BASE_URL = 'http://localhost:8000';

// List servers
async function listServers() {
  const response = await fetch(`${BASE_URL}/api/servers`, {
    headers: {
      'X-API-Key': API_KEY
    }
  });
  const servers = await response.json();
  console.log(`Found ${servers.length} servers`);
  return servers;
}

// Start a server
async function startServer(serverId) {
  const response = await fetch(`${BASE_URL}/api/servers/${serverId}/action`, {
    method: 'POST',
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ action: 'start' })
  });
  const result = await response.json();
  console.log(result);
  return result;
}

listServers();
startServer(1);
```

## API Key Management

### Regenerating Your API Key

If you suspect your API key has been compromised:

1. Go to your profile page
2. Enter the CAPTCHA code
3. Click "Regenerate"
4. Your old API key will be immediately invalidated
5. Update all applications using the old API key with the new one

### Revoking Your API Key

To remove your API key entirely:

1. Go to your profile page
2. Click "Revoke"
3. Confirm the action
4. Your API key will be permanently deleted

## Security Best Practices

1. **Keep your API key secret**: Never share your API key or commit it to version control
2. **Use environment variables**: Store your API key in environment variables, not in your code
3. **Regenerate periodically**: Consider regenerating your API key periodically for enhanced security
4. **Revoke if compromised**: If you suspect your API key has been exposed, revoke it immediately and generate a new one
5. **Use HTTPS**: Always use HTTPS in production to prevent API keys from being intercepted

## API Endpoints for API Key Management

### Get Current API Key

```http
GET /api/auth/api-key
Authorization: Bearer YOUR_JWT_TOKEN
```

Returns your current API key if one exists.

### Generate/Regenerate API Key

```http
POST /api/auth/api-key/generate
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "captcha_token": "token_from_captcha_endpoint",  // Optional
  "captcha_code": "1234"  // Optional
}
```

Generates a new API key (or regenerates if one already exists).

**Note**: CAPTCHA validation is optional. If you're calling this endpoint programmatically via API, you can omit the captcha fields. CAPTCHA is recommended when using the web UI for extra security, but not required for automation.

### Revoke API Key

```http
DELETE /api/auth/api-key
Authorization: Bearer YOUR_JWT_TOKEN
```

Removes your API key permanently.

## Compatibility

- API key authentication works alongside existing JWT token authentication
- You can use either authentication method for any API endpoint that requires authentication
- The system will try JWT authentication first, then fall back to API key authentication
- Both admin and regular users can generate API keys
- Users can only access their own servers regardless of authentication method

## Troubleshooting

### "Invalid API key" Error

- Ensure your API key is correct and hasn't been revoked
- Check that you're including the `X-API-Key` header in your requests
- Verify your user account is still active

### "Could not validate credentials" Error

- This error appears when both JWT and API key authentication fail
- Ensure you're providing either a valid JWT token OR a valid API key
- Check that your API key hasn't expired or been regenerated

### API Key Not Working After Regeneration

- Old API keys are immediately invalidated when you regenerate
- Update all applications/scripts with the new API key
- Clear any cached credentials
