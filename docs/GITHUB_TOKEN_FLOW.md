# GitHub Token Authentication Flow

This document explains how the GitHub token feature works in the CS2 Server Manager.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Profile Page                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  GitHub Personal Access Token: [******************]       │  │
│  │  ☑ Fine-grained token (github_pat_XXX)                   │  │
│  │  ☐ Classic token (ghp_XXX)                               │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Save Token
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Database (users table)                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  id  │  username  │  email  │  github_token               │  │
│  │  1   │  admin     │  a@b.c  │  github_pat_XXXXXX          │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Load on API Request
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Plugin Installation Flow                     │
│                                                                  │
│  1. User clicks "Install Plugin"                                │
│  2. System retrieves user's github_token from database          │
│  3. HTTP Helper adds token to request headers                   │
│  4. Request sent to GitHub API with authentication              │
│  5. GitHub returns data (with better rate limits)               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Request Flow Diagram

### Without Token (Unauthenticated)

```
User → Plugin Market → HTTP Helper → GitHub API
                         │              │
                         │              ▼
                         │         Rate Limit: 60/hour
                         │         Access: Public repos only
                         ▼
                    ❌ Private repos not accessible
```

### With Token (Authenticated)

```
User → Plugin Market → HTTP Helper ──┐
          │               │           │
          │               │           ▼
          │               │      Add Header:
          │               │      Authorization: Bearer {token}
          │               │           │
          │               ▼           ▼
          │          GitHub API ──────┘
          │               │
          ▼               ▼
    github_token    ✅ Rate Limit: 5000/hour
    from DB         ✅ Access: Private repos
                    ✅ Better reliability
```

## Component Interaction

```
┌──────────────────┐
│   User Profile   │ ← User enters token
└────────┬─────────┘
         │
         │ POST /api/auth/profile
         │ {github_token: "github_pat_XXX"}
         ▼
┌──────────────────┐
│  Auth Endpoint   │ ← Validates and saves token
└────────┬─────────┘
         │
         │ Save to database
         ▼
┌──────────────────┐
│   User Model     │ ← github_token field
└──────────────────┘
         │
         │ Load when needed
         ▼
┌──────────────────┐
│  HTTP Helper     │ ← Adds Authorization header
└────────┬─────────┘
         │
         │ Authorization: Bearer {token}
         ▼
┌──────────────────┐
│   GitHub API     │ ← Returns data with auth
└──────────────────┘
```

## Data Flow

### 1. Token Storage

```
Input: github_pat_11AAAAAAA0AaAaAaAaAa...
  │
  ├─► Validation (regex pattern)
  │
  ├─► Strip whitespace
  │
  └─► Store in database
       ├─► Field: users.github_token
       └─► Type: VARCHAR(255)
```

### 2. Token Usage

```
API Request Initiated
  │
  ├─► Check if user has token
  │     └─► user.has_github_token
  │
  ├─► Load token from database
  │     └─► user.github_token
  │
  ├─► Validate URL is GitHub API
  │     └─► url.startswith('https://api.github.com/')
  │
  ├─► Add to headers
  │     └─► Authorization: Bearer {token}
  │
  └─► Send request
        └─► Better rate limits & access
```

## Security Flow

```
┌──────────────────────────────────────────────────────────────┐
│                      Security Layers                          │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Input Validation                                         │
│     └─► Regex pattern matching (fine-grained & classic)     │
│                                                               │
│  2. UI Security                                              │
│     └─► Password input type (token hidden)                  │
│                                                               │
│  3. Transmission Security                                    │
│     └─► Authorization header only (never in URLs)           │
│                                                               │
│  4. Storage                                                  │
│     └─► Database VARCHAR field (not encrypted*)             │
│                                                               │
│  5. Validation Before Use                                    │
│     └─► Check for empty/whitespace                          │
│                                                               │
│  6. Scope Limitation                                         │
│     └─► Only used for api.github.com requests               │
│                                                               │
└──────────────────────────────────────────────────────────────┘

* Note: Token is a user-provided API key, not a password.
  Users should configure fine-grained tokens with minimal permissions.
```

## Rate Limiting Impact

```
┌─────────────────────────────────────────────────────────────┐
│              GitHub API Rate Limits                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Without Token (IP-based)                                   │
│  ────────────────────────                                   │
│   60 requests/hour                                           │
│   │                                                          │
│   ├─► Shared across all users on same IP                   │
│   ├─► Easily exhausted                                      │
│   └─► Causes installation failures                          │
│                                                              │
│                         VS                                   │
│                                                              │
│  With Token (User-based)                                    │
│  ───────────────────────                                    │
│   5,000 requests/hour                                        │
│   │                                                          │
│   ├─► Per-user limit                                        │
│   ├─► 83x improvement                                       │
│   └─► Reliable operations                                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Token Types Comparison

```
┌──────────────────────┬──────────────────────┬──────────────────┐
│   Feature            │  Fine-grained        │  Classic         │
├──────────────────────┼──────────────────────┼──────────────────┤
│  Prefix              │  github_pat_         │  ghp_, gho_, ... │
│  Security            │  ✅ Granular         │  ⚠️ Broad       │
│  Repository Scope    │  ✅ Specific repos   │  ❌ All repos   │
│  Permission Control  │  ✅ Fine-grained     │  ❌ All or none │
│  Expiration          │  ✅ Customizable     │  ✅ Customizable│
│  Recommended         │  ✅ Yes              │  ❌ No          │
└──────────────────────┴──────────────────────┴──────────────────┘
```

## Usage Example

### Scenario: Installing a Private Plugin

```
Step 1: User configures token in profile
  └─► Token: github_pat_11AAAAAAA0AaAaAaAaAa...
  └─► Saved to: users.github_token

Step 2: User navigates to Plugin Market
  └─► Selects private plugin
  └─► Clicks "Install"

Step 3: System fetches plugin releases
  └─► github_plugins.py calls GitHub API
  └─► http_helper.py adds Authorization header
  └─► Request includes: Bearer {user's token}

Step 4: GitHub API responds
  └─► User is authenticated
  └─► Private repo accessible
  └─► Release data returned

Step 5: Plugin installed successfully
  └─► Download from private repo
  └─► Extract to server
  └─► Complete!
```

## Error Handling Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Error Scenarios                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Invalid Token Format                                        │
│  ────────────────────                                        │
│   Input: invalid_token_123                                   │
│     │                                                         │
│     └─► Validation fails (regex mismatch)                   │
│         └─► Error: "Token must be valid..."                 │
│             └─► User sees error message                     │
│                                                              │
│  Expired Token                                               │
│  ──────────────                                              │
│   Request sent with expired token                            │
│     │                                                         │
│     └─► GitHub returns 401 Unauthorized                     │
│         └─► Error shown to user                             │
│             └─► User needs to regenerate token              │
│                                                              │
│  No Token (Private Repo)                                     │
│  ────────────────────────                                    │
│   Request to private repo without token                      │
│     │                                                         │
│     └─► GitHub returns 404 Not Found                        │
│         └─► Error: "Repository not found"                   │
│             └─► User needs to configure token               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Benefits Visualization

```
                    Before                    After
                    ──────                    ─────
Rate Limiting      60/hour    ────────►     5000/hour
                   
Private Repos      ❌ No      ────────►     ✅ Yes
                   
Reliability        ⚠️ Low     ────────►     ✅ High
                   
Setup Time         0 min      ────────►     2 min
                   
User Effort        None       ────────►     Minimal
                   
Security           N/A        ────────►     Fine-grained
                   
Cost               Free       ────────►     Free
```
