# Auto-Restart Feature Documentation

## Overview

The CS2 Server Manager now includes an advanced auto-restart feature that provides automatic server recovery with crash loop protection and real-time status reporting to the web management backend.

## Features

### 1. Automatic Server Restart

When a CS2 server crashes or exits unexpectedly, the system automatically attempts to restart it using an `until` loop pattern, eliminating the need for manual intervention.

### 2. Crash Loop Protection

To prevent infinite restart loops when a server has persistent issues, the system implements intelligent crash tracking:

- **Maximum Restarts**: 5 restarts within a 10-minute window
- **Time Window**: 600 seconds (10 minutes)
- **Restart Delay**: 10-second cooldown between restart attempts

When the crash limit is reached, the system:
- Stops attempting automatic restarts
- Reports the issue to the management backend
- Updates server status to "STOPPED"
- Requires manual intervention to restart

### 3. Real-Time Status Reporting

The server communicates with the management backend via secure API calls to report:

- **Startup Events**: When the server starts successfully
- **Crash Events**: When the server crashes with exit code details
- **Restart Events**: When automatic restart is triggered
- **Shutdown Events**: When the server stops gracefully
- **Crash Limit Reached**: When auto-restart is disabled due to excessive crashes

### 4. Secure API Key Authentication

Each server is assigned a unique 64-character API key for secure communication:
- Auto-generated when creating new servers
- Used to authenticate status reports
- Prevents unauthorized status updates

## Architecture

### Components

1. **Auto-Restart Wrapper Script** (`cs2_autorestart.sh`)
   - Bash script that wraps the CS2 server process
   - Implements `until` loop for continuous restart
   - Tracks crash history in a log file
   - Sends status reports via curl POST requests

2. **Server Status API** (`/api/server-status`)
   - RESTful endpoint for receiving status reports
   - Authenticates requests via API key headers
   - Updates server status in database
   - Creates deployment log entries

3. **Enhanced Start Server Logic**
   - Deploys autorestart script to remote server
   - Configures environment with server_id, api_key, and backend_url
   - Starts server within screen session for persistence

## Usage

### Automatic Usage

The auto-restart feature is automatically enabled when you start a server through the web interface or API. No additional configuration is required.

### Manual Configuration

If you need to customize the auto-restart behavior, you can modify the parameters in the wrapper script:

```bash
# Edit on the remote server
vim /path/to/game/directory/cs2_autorestart.sh

# Configuration variables
MAX_RESTARTS=5          # Maximum restarts in time window
TIME_WINDOW=600         # Time window in seconds
RESTART_DELAY=10        # Delay between restarts
```

### Environment Variables

Configure the backend URL in your `.env` file:

```env
BACKEND_URL=http://your-backend-url:8000
```

This URL is used by the autorestart script to report status updates.

## API Endpoints

### Report Server Status

**Endpoint**: `POST /api/server-status/{server_id}/report`

**Headers**:
```
Content-Type: application/json
X-API-Key: <server-api-key>
```

**Request Body**:
```json
{
  "event_type": "crash|restart|startup|shutdown|crash_limit_reached",
  "message": "Optional status message",
  "exit_code": 1,
  "restart_count": 3,
  "crash_details": "Optional crash details"
}
```

**Response**:
```json
{
  "success": true,
  "message": "Status report received",
  "server_id": 1,
  "event_type": "restart",
  "current_status": "running"
}
```

### Get Server Configuration

**Endpoint**: `GET /api/server-status/{server_id}/config`

**Headers**:
```
X-API-Key: <server-api-key>
```

**Response**:
```json
{
  "server_id": 1,
  "name": "My CS2 Server",
  "game_port": 27015,
  "default_map": "de_dust2",
  "max_players": 32,
  "tickrate": 128,
  "game_mode": "competitive",
  "game_type": "0"
}
```

## Monitoring

### Crash History Log

The autorestart script maintains a crash history log on each server:

**Location**: `{game_directory}/crash_history.log`

**Format**:
```
1700000000 2023-11-14 12:00:00
1700000610 2023-11-14 12:10:10
```

Each line contains:
- Unix timestamp
- Human-readable date/time

### Deployment Logs

Status reports are automatically logged in the database under deployment logs with action types:
- `auto_startup`
- `auto_crash`
- `auto_restart`
- `auto_shutdown`
- `auto_crash_limit_reached`

## Troubleshooting

### Server Won't Start with Auto-Restart

If the autorestart script fails to deploy:

1. Check server logs for error messages
2. Verify the server has the required tools installed:
   ```bash
   command -v curl
   command -v screen
   ```
3. The system will fallback to simple start without auto-restart

### API Key Issues

If status reports aren't being received:

1. Verify the API key exists:
   ```sql
   SELECT id, name, api_key FROM servers WHERE id = <server_id>;
   ```

2. Regenerate the API key if needed (through database or API)

3. Check the backend URL configuration matches your actual backend

### Crash Loop Protection Triggered

If a server stops due to crash loop protection:

1. Check deployment logs for crash details
2. Review server console logs for error patterns
3. Fix the underlying issue (map errors, missing files, etc.)
4. Manually restart the server through the web interface

The crash history will be cleared after the 10-minute window expires.

## Security Considerations

1. **API Key Storage**: API keys are stored in the database and should be kept secure
2. **HTTPS Recommended**: Use HTTPS for production deployments to encrypt status reports
3. **Firewall Rules**: Ensure servers can reach the backend URL
4. **Rate Limiting**: Consider implementing rate limiting on status reporting endpoints

## Migration

### Database Migration

The database migration to add the `api_key` field is automatic and runs on application startup. For manual migration:

```bash
# Run the migration SQL script
mysql -u cs2_manager -p cs2_manager < migrations/add_server_api_key.sql
```

### Existing Servers

Existing servers created before this update will:
- Not have API keys initially
- Require updating to generate API keys
- Work without auto-restart until API keys are generated

To add API keys to existing servers:

```python
from modules import generate_api_key
from sqlalchemy import update

# Update all servers without API keys
update_stmt = (
    update(Server)
    .where(Server.api_key == None)
    .values(api_key=generate_api_key())
)
```

## Performance Impact

- **Minimal CPU Overhead**: The wrapper script uses minimal resources
- **Network Traffic**: Status reports are small JSON payloads (~200 bytes each)
- **Disk I/O**: Crash log is append-only with automatic cleanup
- **Memory**: No additional memory overhead

## Future Enhancements

Potential future improvements:
- Configurable restart parameters per server
- Email/webhook notifications for crash events
- Advanced crash analysis and recommendations
- Web UI for viewing crash history
- Automatic backup before restart attempts
