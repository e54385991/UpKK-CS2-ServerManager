# SSH Health Monitoring System

## Overview

The SSH Health Monitoring system is an independent background daemon that continuously monitors SSH connectivity to all managed servers. It provides automatic recovery from transient issues and prevents resource waste on permanently unreachable servers.

## Key Features

### 1. **Independent Background Daemon**
- Runs completely independently in a background thread
- Non-blocking - does not interfere with normal server operations
- Automatic startup with the application
- Configurable check intervals (default: 2 hours)

### 2. **Progressive Failure Detection**
The system uses a multi-tier approach to server health:

- **Healthy**: SSH connection successful, no issues
- **Degraded**: 1-2 consecutive failures, server may be temporarily unreachable
- **Down**: 3+ consecutive failures, server marked as down
- **Completely Down**: 84+ consecutive failures (7 days at 2-hour intervals)

### 3. **Automatic Recovery**
- Automatically detects when a previously down server becomes available
- Resets failure counters on successful connection
- No manual intervention needed for transient issues

### 4. **Resource Protection**
- Servers in "completely_down" state are not automatically checked
- Prevents wasting resources on permanently offline servers
- Requires manual reconnection from admin UI to restore

### 5. **Visual Status Indicators**
Server cards in `/servers-ui` show:
- SSH connectivity status with color coding
- Consecutive failure count
- Estimated offline duration
- Last probe time
- Manual reconnect button for completely down servers

## Architecture

### Database Schema

New fields added to the `servers` table:

```sql
-- SSH health monitoring configuration
enable_ssh_health_monitoring TINYINT(1) DEFAULT 1
ssh_health_check_interval_hours INT DEFAULT 2
ssh_health_failure_threshold INT DEFAULT 84
last_ssh_health_check TIMESTAMP NULL
ssh_health_status VARCHAR(50) DEFAULT 'unknown'
```

### Service Components

#### 1. SSH Health Monitor Service (`services/ssh_health_monitor.py`)

Main daemon service that:
- Runs continuous background loop
- Checks all servers with monitoring enabled
- Respects individual server check intervals
- Updates health status in database
- Provides manual reconnection API

#### 2. Database Migrations (`modules/database.py`)

Automatic schema migrations that:
- Add new columns if they don't exist
- Set appropriate defaults
- Maintain backward compatibility

#### 3. API Endpoints (`api/routes/servers.py`)

Two new endpoints:

**GET `/servers/{server_id}/ssh-health`**
- Returns detailed SSH health status
- Calculates offline duration estimates
- Requires authentication

**POST `/servers/{server_id}/ssh-reconnect`**
- Manually tests connection and resets health status
- Used for "completely_down" servers
- Requires authentication

#### 4. UI Components (`templates/servers.html`)

- Server card displays SSH health status
- Color-coded status indicators
- Failure count and offline estimate
- Manual reconnect button
- i18n support for all text

## Configuration

### Server-Level Settings

Each server has individual configuration:

```python
enable_ssh_health_monitoring: bool = True  # Enable/disable monitoring
ssh_health_check_interval_hours: int = 2   # How often to check (hours)
ssh_health_failure_threshold: int = 84     # Failures before completely_down
```

### Default Behavior

By default, all servers have:
- Monitoring enabled
- 2-hour check interval
- 84-failure threshold (7 days)

This means a server will be checked every 2 hours, and after 84 consecutive failures (approximately 7 days), it will be marked as "completely_down".

## Status Transitions

```
unknown → healthy (on first successful check)
healthy → degraded (on first failure)
degraded → healthy (on successful check)
degraded → down (after 3+ failures)
down → healthy (on successful check)
down → completely_down (after 84+ failures)
completely_down → healthy (only via manual reconnect)
```

## Usage Guide

### For End Users

1. **View SSH Health Status**
   - Navigate to `/servers-ui`
   - Each server card shows SSH connectivity status
   - Color coding:
     - Green: Healthy
     - Yellow: Degraded
     - Red: Completely Down

2. **Manual Reconnection**
   - If a server shows "Completely Down"
   - Click the reconnect button (⟳)
   - System will test connection and reset status if successful

### For Administrators

1. **Monitoring Configuration**
   - Edit server in database or via API
   - Adjust `ssh_health_check_interval_hours` for different check frequencies
   - Adjust `ssh_health_failure_threshold` for different tolerance levels

2. **Disable Monitoring**
   - Set `enable_ssh_health_monitoring = False` for specific servers
   - Useful for servers that are intentionally offline

## Technical Details

### Check Process

1. Daemon loop runs every 60 seconds
2. For each server with monitoring enabled:
   - Calculate next check time based on interval
   - Skip if not time yet
   - Skip if checked within last 30 seconds (dedup)
   - Skip if status is "completely_down"
3. Attempt SSH connection with 10-second timeout
4. Update database with result
5. Log status changes

### Failure Tracking

```python
# On failure
consecutive_ssh_failures += 1
last_ssh_failure = now
is_ssh_down = (consecutive_ssh_failures >= 3)
ssh_health_status = determine_status(consecutive_ssh_failures, threshold)

# On success
consecutive_ssh_failures = 0
last_ssh_success = now
is_ssh_down = False
ssh_health_status = "healthy"
```

### Performance Considerations

- Uses connection pooling for efficiency
- Short 10-second timeout prevents hanging
- Non-blocking async operations
- Database updates are quick and atomic
- No performance impact on server operations

## Logging

The system logs important events:

```python
# Info level
- Monitoring start/stop
- Health checks performed
- Status changes

# Warning level
- Degraded status
- Failure threshold reached

# Error level
- Servers marked completely down
- Connection errors
```

## API Response Examples

### SSH Health Status

```json
{
  "server_id": 1,
  "ssh_health_status": "degraded",
  "consecutive_failures": 5,
  "failure_threshold": 84,
  "is_ssh_down": true,
  "last_ssh_success": "2024-01-01T10:00:00",
  "last_ssh_failure": "2024-01-01T20:00:00",
  "last_health_check": "2024-01-01T20:00:00",
  "check_interval_hours": 2,
  "offline_duration_estimate": {
    "hours": 10,
    "days": 0.4,
    "description": "~10 hours (0.4 days)"
  },
  "monitoring_enabled": true
}
```

### Manual Reconnect Success

```json
{
  "success": true,
  "message": "SSH connection successful - server health restored",
  "ssh_health_status": "healthy"
}
```

### Manual Reconnect Failure

```json
{
  "success": false,
  "message": "SSH connection failed - server is still unreachable",
  "ssh_health_status": "completely_down"
}
```

## Troubleshooting

### Issue: Server not being monitored

**Check:**
1. `enable_ssh_health_monitoring` is `True`
2. Daemon is running (check startup logs)
3. Server credentials are correct

### Issue: False positives (server marked down but is up)

**Solutions:**
1. Increase `ssh_health_check_interval_hours` if network is slow
2. Check firewall rules
3. Verify SSH service is running on server
4. Check authentication credentials

### Issue: Too many checks

**Solution:**
- Increase `ssh_health_check_interval_hours` to reduce frequency
- Default is 2 hours, can be set to 4, 6, 12, or 24 hours

## Future Enhancements

Possible improvements:

1. **Email/Webhook Notifications**
   - Alert when server goes down
   - Alert when completely_down threshold reached

2. **Configurable Check Patterns**
   - Different intervals for different times of day
   - Aggressive checking during business hours

3. **Health Trends**
   - Track historical uptime/downtime
   - Generate availability reports

4. **Smart Thresholds**
   - Adaptive thresholds based on server history
   - Different thresholds for different server types

## Integration with Existing Features

### SSH Connection Pool
- Health monitor uses connection pool when available
- Respects existing connection management
- No duplicate connections

### Server Operations
- Operations check `is_ssh_down` before attempting
- Prevents wasting time on known-down servers
- User informed if server is down

### Auto-Restart
- Auto-restart respects SSH health status
- Won't attempt restart on completely_down servers
- Prevents restart loops

## Migration Notes

When upgrading to this version:

1. Database migrations run automatically on startup
2. All existing servers get default monitoring enabled
3. Initial status is "unknown" until first check
4. First check happens within 2 hours of startup
5. No action required from users

## Summary

The SSH Health Monitoring system provides:
- ✅ Automatic background monitoring
- ✅ Progressive failure detection
- ✅ Automatic recovery
- ✅ Resource protection
- ✅ Visual status indicators
- ✅ Manual reconnection capability
- ✅ Configurable intervals and thresholds
- ✅ Non-blocking operation
- ✅ Full i18n support

This enhancement significantly improves server management by providing proactive monitoring and intelligent resource management for SSH connectivity.
