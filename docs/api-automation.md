# Personal API keys for server automation

Sign in to the console and generate or copy your **personal user API key** from Profile. Send it as `X-API-Key` to the backend API origin. This is separate from a game server's status-reporting key. HTTPS is recommended.

Personal keys are accepted only on these endpoints:

| Method | Path |
| --- | --- |
| GET | `/api/v1/auth/me` |
| GET | `/api/v1/servers` |
| GET | `/api/v1/servers/{server_id}` |
| POST | `/api/v1/servers/{server_id}/operations` |
| GET | `/api/v1/servers/{server_id}/operations/current` |
| GET | `/api/v1/servers/{server_id}/operations/{operation_id}` |

The key inherits its active owner's existing permissions: ordinary accounts can access their own servers; administrators can access the fleet and use `scope=all` when listing servers. Revoked keys and inactive accounts fail authentication on the next request. Key authentication does not grant access to profile secrets, key generation, server creation/deletion, arbitrary console commands, operation cancellation, or other routes. These retain their existing authentication paths.

Bearer JWT authentication continues to work. If a Bearer token is supplied, it is authoritative: an invalid or expired token does not fall back to the key. Internal FastAPI scope `personal_api_key` marks the six opted-in route/method pairs; it is not a new role or a required JWT claim.

Submit a lifecycle action, for example `{"action":"restart"}`, to the operation endpoint. It returns **202** and an `operation_id` immediately; execution stays in the existing per-server FIFO and retains the current lifecycle policies and actor audit identity. Poll the specific operation endpoint for `queued`, `running`, `completed`, or `failed`. Clients must not automatically repeat an uncertain POST: verify the queue and operation history first. A personal key can use the lifecycle actions accepted by that endpoint, subject to existing access checks; clients may expose a smaller command set.

The AstrBot UPKK CS2 plugin adds a separate AstrBot-administrator check, server allowlisting, explicit confirmation, and durable deduplication. It also supports CAPTCHA-aware password login for JWT-only deployments. Its dashboard and chat permissions do not change Manager account permissions.

This authentication update changes no database schema and requires no new credentials or configuration settings. Deploy the updated backend normally before using personal-key automation. Installing a client plugin does not update an already running backend. Local tests exercise the actual FastAPI dependency graph using fake repositories and queue workers; they do not execute SSH or restart live servers.
