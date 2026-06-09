# CS2 Server Auto-Update Guide

## Overview

The CS2 Server Manager now includes an automatic update feature that monitors the version of CS2 servers and automatically updates them when a new version is released by Valve.

## How It Works

1. **Version Monitoring**: The system periodically queries each server using the A2S protocol to detect the current game version
2. **Version Checking**: Every hour, the auto-update service checks the Steam API to see if a new CS2 version is available
3. **Automatic Updates**: When a new version is detected and auto-update is enabled for a server, the system:
   - Runs `steamcmd` to update the server files
   - Automatically restarts the server after the update completes
4. **Rate Limiting**: Version checks are limited to once per hour per server to avoid excessive API calls

## Features

- ✅ **Automatic version detection** from A2S queries
- ✅ **Steam API integration** for version checking
- ✅ **Hourly version checks** (configurable per server)
- ✅ **Automatic server updates** when new version detected
- ✅ **Automatic restart** after successful update
- ✅ **Per-server configuration** (enable/disable auto-update)
- ✅ **Version display** in the web interface
- ✅ **Last update timestamp** tracking

## Configuration

### Enable/Disable Auto-Update

Auto-update is **enabled by default** for all servers. You can configure this per-server:

1. Navigate to the server management page
2. Edit server settings
3. Toggle the `enable_auto_update` field

### Database Fields

The following fields have been added to the `servers` table:

- `current_game_version` (VARCHAR(50)): Current installed CS2 version
- `enable_auto_update` (BOOLEAN): Enable/disable auto-update (default: TRUE)
- `last_update_check` (DATETIME): Timestamp of last version check
- `last_update_time` (DATETIME): Timestamp of last successful update

### Migration

Run the migration script to add the new fields:

```sql
mysql -u cs2admin -p cs2_manager < migrations/add_auto_update_version_fields.sql
```

Or the script will run automatically on application startup.

## UI Display

### Server List Page (`/servers-ui`)

The server list now displays:
- **Game Version**: Current CS2 version (e.g., "1.41.2.5")
- **Last Updated**: Relative time since A2S data was received (e.g., "5m ago", "2h ago")

This information appears in the server card when A2S query is successful.

## Steam API Integration

### Endpoint

The system uses the Steam API endpoint:
```
https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/
```

### Parameters

- `appid`: 730 (CS2 app ID)
- `version`: Current server version
- `format`: json

### Example Response

```json
{
  "response": {
    "success": true,
    "up_to_date": false,
    "required_version": 14125,
    "message": "Server version required: 1.41.2.5"
  }
}
```

## Background Services

### A2S Cache Service

- Queries all servers every 30 seconds
- Updates `current_game_version` in database when detected
- Caches results in Redis with 60-second TTL

### Auto-Update Service

- Runs continuously in the background
- Checks servers with `enable_auto_update = TRUE` every hour
- Only checks if `last_update_check` is older than 1 hour
- Triggers update when `up_to_date = false` from Steam API

## Monitoring and Logs

Check the application logs for auto-update activity:

```bash
# Look for auto-update messages
tail -f /var/log/cs2-manager.log | grep -i "auto-update"

# Example log messages:
# [INFO] Auto-update service started
# [INFO] Checking 5 servers with auto-update enabled
# [INFO] Server 1 (CS2-Server-01) needs update: current=1.41.2.4, required=1.41.2.5
# [INFO] Triggering auto-update for server 1 (CS2-Server-01)
# [INFO] Update successful for server 1
# [INFO] Restarting server 1 after update
# [INFO] Auto-update completed successfully for server 1
```

## Troubleshooting

### Auto-update not working

1. **Check auto-update is enabled**:
   ```sql
   SELECT id, name, enable_auto_update, last_update_check 
   FROM servers 
   WHERE id = YOUR_SERVER_ID;
   ```

2. **Verify Steam API connectivity**:
   ```bash
   curl "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/?appid=730&version=1&format=json"
   ```

3. **Check application logs** for error messages

4. **Verify A2S queries are working**:
   - Check the server list page shows version information
   - Verify `current_game_version` is populated in database

### Version not detected

1. **Ensure server is running** and responding to A2S queries
2. **Check A2S query host/port** are configured correctly
3. **Verify firewall** allows UDP queries to the game port

### Update fails

1. **Check SSH connectivity** to the server
2. **Verify steamcmd** is installed on the target server
3. **Check disk space** on target server
4. **Review update logs** in the deployment logs table

## Manual Override

You can manually trigger an update without waiting for auto-update:

1. Navigate to server detail page
2. Click "Update Server" button
3. Or use the API: `POST /servers/{id}/actions` with `action: "update"`

## API Integration

### Check Server Version

```bash
GET /servers/{id}
```

Response includes:
```json
{
  "id": 1,
  "name": "CS2-Server-01",
  "current_game_version": "1.41.2.5",
  "enable_auto_update": true,
  "last_update_check": "2024-11-19T03:00:00Z",
  "last_update_time": "2024-11-19T02:30:00Z",
  ...
}
```

### Update Server Settings

```bash
PUT /servers/{id}
Content-Type: application/json

{
  "enable_auto_update": false
}
```

## Security Considerations

- Auto-update only runs with the SSH credentials configured for each server
- Updates are isolated per server - a failed update on one server doesn't affect others
- Rate limiting prevents excessive API calls to Steam
- All update operations are logged for audit purposes

## Performance Impact

- **A2S queries**: Minimal impact, runs every 30 seconds in background
- **Version checks**: Once per hour per server, very lightweight
- **Updates**: Only run when needed, server is restarted automatically
- **Redis cache**: Keeps memory usage low with 60-second TTL

## Future Enhancements

Potential improvements for future versions:
- Email/webhook notifications when updates occur
- Scheduled update windows (e.g., only update during off-peak hours)
- Rollback capability if update causes issues
- Update staging (test on one server before updating all)
- Custom update scripts/hooks

## Support

For issues or questions:
1. Check the application logs
2. Review this guide
3. Create an issue on GitHub
4. Contact the maintainers

---

**Note**: This feature requires CS2 servers to respond to A2S queries. Ensure your firewall allows UDP traffic on the game port.
