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
| GET | `/api/v1/automation/game-commands` |
| POST | `/api/v1/servers/{server_id}/automation-operations` |
| GET | `/api/v1/servers/{server_id}/automation-status` |
| GET | `/api/v1/servers/{server_id}/plugin-updates` |
| POST | `/api/v1/servers/{server_id}/plugin-updates/run` |

The key inherits its active owner's existing permissions: ordinary accounts can access their own servers; administrators can access the fleet and use `scope=all` when listing servers. Revoked keys and inactive accounts fail authentication on the next request. Key authentication does not grant access to profile secrets, key generation, server creation/deletion, arbitrary console commands, operation cancellation, or other routes. These retain their existing authentication paths.

Bearer JWT authentication continues to work. If a Bearer token is supplied, it is authoritative: an invalid or expired token does not fall back to the key. Internal FastAPI scope `personal_api_key` marks the explicitly opted-in route/method pairs; it is not a new role or a required JWT claim.

Submit a lifecycle action, for example `{"action":"restart"}`, to the operation endpoint. It returns **202** and an `operation_id` immediately; execution stays in the existing per-server FIFO and retains the current lifecycle policies and actor audit identity. Poll the specific operation endpoint for `queued`, `running`, `completed`, or `failed`. Clients must not automatically repeat an uncertain POST: verify the queue and operation history first. A personal key can use the lifecycle actions accepted by that endpoint, subject to existing access checks; clients may expose a smaller command set.

## Restricted automation

`POST /api/v1/servers/{server_id}/automation-operations` accepts exactly one of:

```json
{"action":"restart"}
```

```json
{"action":"send_game_command","command":"css_wsmap","args":["ze_example"]}
```

Both return the existing task DTO with **202 + operation_id**, use the per-server FIFO, and recheck the actor's current account/server access in the worker. Console tasks always have action `send_game_command`. The queue preserves structured command arguments and a safety-policy marker; the worker resolves the registry again rather than trusting a raw console line. Unknown commands, extra request fields, wrong arity, controls and command separators are rejected before enqueue and checked again before execution. Delivery uses the existing detached game session channel, never a shell-command API. Successful console delivery does not verify the game-side effect or map transition.

`GET /api/v1/automation/game-commands` discovers names, descriptions, argument contracts and online/empty policies. `css_wsmap` accepts exactly one map name matching the complete ASCII pattern `[A-Za-z0-9_]{1,128}`. `css_ext_maptime` accepts exactly one positive ASCII integer from 1 to 86400 seconds; for example, `{"action":"send_game_command","command":"css_ext_maptime","args":["3600"]}`. The API always requires explicit seconds. AstrBot shortcuts and the generic console entry supply 3600 when seconds are omitted and retain configured command switches, public server IDs, confirmation, shared cooldown, and deduplication. AstrBot v0.3.5 allows ordinary users to use the extension shortcut when its separate permission is enabled; generic console remains administrator-only. Fresh plugin configurations enable both registered commands; existing saved command selections remain unchanged and need an explicit switch for the new command. The time-extension command requires an online server, permits human players, and does not weaken the zero-human-player requirements for restart or map changes. Queue completion confirms delivery, not the resulting map time; the game server must have a plugin implementing `css_ext_maptime`.

## Personal command registry

Open **Profile > Game commands** (`/settings/profile#profile-game-commands`) to add, edit, disable, or remove commands. Changes are scoped to the signed-in Manager account, including that account's personal API key. Each entry defines its single console command name, description, ordered arguments, enabled state, and zero-human-player requirement. Every registered game command requires a successful live A2S observation; there is no offline game-console mode. No restart or deployment is needed after saving.

Argument types are ASCII tokens (`[A-Za-z0-9_]`), canonical nonnegative integer ranges, or a fixed list of ASCII tokens. Names are bounded to 64 characters, tokens to 128 characters, commands to 100 per account and arguments to 16 per command. Patterns are generated by the server and bounded to 512 characters; arbitrary regular expressions, quoted multiword strings, separators and control characters are not supported. Built-in `css_wsmap` and `css_ext_maptime` argument contracts remain fixed, and map changes cannot disable the zero-human-player guard. Both can be disabled or removed. Changing or removing a game command does not grant any additional server access.

The console uses JWT-only `GET` and `PUT /api/v1/profile/game-commands` with a `{"commands":[...]}` object. Personal API keys cannot edit the registry. `GET /api/v1/automation/game-commands` returns only the requesting account's enabled runtime definitions; its existing response shape and parameter patterns remain compatible with bot clients. The worker reloads the actor's current registry after obtaining the maintenance lock and rebuilds the command from validated arguments, so disabling/removing a command or tightening its rules can reject a queued request. No unchecked raw-command fallback is used.

Existing accounts with no saved registry retain the original two defaults. Saving an empty list disables all registered automation commands for that account. The `users.automation_commands` JSONB column is added by the normal startup migration. In AstrBot, refresh the command registry and enable the new entry after saving. Custom commands use `/upkk_cs2 console SERVER_ID COMMAND [ARGS...] [confirm]`, which remains administrator-only. Built-in ordinary-user switches, cooldowns and the preview/manual-confirmation workflow remain enforced by the bot. Registration does not install the game plugin that implements the command.

Run the repository baseline before starting the isolated browser regression:

```bash
uv run python scripts/check_baseline.py
cd frontend
npx playwright test --config=playwright.profile-commands.config.ts
```

The browser fixture uses loopback-only fake API state and real Next.js/Chromium. It covers adding, saving, reloading, disabling, and retaining failed edits at 390 and 1440 pixels. It does not use real credentials or contact a game server. Do not run this Next.js dev fixture concurrently with a production build in the same checkout. Backend regression coverage lives in `tests/test_personal_game_commands.py`; a configured PostgreSQL integration environment is still needed to verify the migration against a real database.

Workers hold the existing maintenance lock and use a fresh, bounded A2S probe under the shared limiter, never stale Redis data. Only valid integer counts with `0 <= bot_count <= player_count <= 255` qualify; human count is the difference. `css_wsmap` requires an online server without humans. Public restart checks again immediately before stopping. A failed A2S query cannot prove emptiness: offline recovery requires SSH to confirm the target detached session is stopped, no related CS2/wrapper process runs, the game port is not listening, and the session remains stopped after the scans. Failed or ambiguous evidence refuses execution. Administrator-requested stop state blocks automation. Occupancy checks cannot be atomic with player connections.

The AstrBot UPKK CS2 plugin v0.3.0 retains administrator management and adds live player counts, BOT/human separation, game version, restart eligibility, and guided help. `automation-status` authorizes the server before releasing the request database session, then performs one bounded live A2S query through the shared limiter. It returns only game observations and advisory restart eligibility; query failure or invalid counts never prove emptiness. Manual-stop requests, active operations and maintenance locks can deny eligibility. It neither starts SSH probes nor submits operations, and execution-time restart guards remain authoritative.

The plugin's administrator `restart` now uses `automation-operations`, retaining zero-human-player checks rather than calling unrestricted lifecycle restart. Its public automation retains explicit `public_server_ids`, registered command switches, per-server cooldowns (10 minutes by default), preview/confirmation, public-only operation lookup and durable SQLite deduplication across reloads. Accepted failures consume cooldown; explicit HTTP rejection does not. Timeout/cancellation/uncertain submission keeps its reservation without automatic retry. The cooldown is enforced by that bot installation, not globally across independent API clients. Its full Management page uses native configuration persistence, write-only API key input, CAPTCHA-aware temporary login and safe connection switching. Public operations require a configured personal key. Its dashboard/chat permissions do not expand Manager account access.

The two opted-in `plugin-updates` routes retain existing owner/admin access. Reading the workspace returns tracked plugins and saved versions; it does not check upstream releases. `POST .../plugin-updates/run` queues the same check-and-install operation as the console Updates page, returns 202, and retains the existing audit identity, backup/exclusion rules, configured batch restart, and post-update commands. The chatbot restricts this action to AstrBot administrators and requires an explicit current-message confirmation. It does not expose update configuration, registration, removal, or other plugin routes to personal keys. Configured update restarts keep their existing panel policy.

Deploy this Manager update first, then install the plugin update, save/test the connection, and explicitly configure public server IDs. The personal-registry migration runs automatically at Manager startup. Local FastAPI, worker, A2S/SSH-fake and UI tests do not establish live QQ delivery, SSH compatibility on a particular host, actual map transition or production rendering behavior.

The earlier authentication update itself requires no new credentials; the personal registry adds the automatically migrated column described above. Deploy the updated backend normally before using personal-key automation. Installing a client plugin does not update an already running backend. Local tests exercise the actual FastAPI dependency graph using fake repositories and queue workers; they do not execute SSH or restart live servers.
