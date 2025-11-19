# A2S Query Support - Feature Documentation

## Overview

The CS2 Server Manager now supports **A2S (Address to Server) Query Protocol** for querying CS2 server information in real-time. This feature allows you to:

1. Query server information (server name, map, players, etc.)
2. Monitor server health using A2S instead of SSH process checks
3. Configure custom query addresses/ports if different from the game server
4. Set failure thresholds for automatic restart triggers

## Installation / Migration

### Automatic Migration

The database migration will run automatically when you start the application. The Python code in `modules/database.py` will check for and add the necessary columns.

### Manual Migration

If you prefer to run the migration manually, you have two options:

#### Option 1: Simple Migration (Recommended)
Run `migrations/add_a2s_query_support_simple.sql` - This runs each ALTER TABLE statement individually. If a column already exists, you'll get an error for that specific statement, which you can safely ignore.

#### Option 2: Safe Migration with Checks
Run `migrations/add_a2s_query_support.sql` - This uses prepared statements to check if columns exist before adding them. More complex but safer for repeated runs.

**Note:** After running the migration, restart your application to ensure all changes take effect.

## What is A2S?

A2S is the Source Engine's standard query protocol that allows external applications to query game server information. CS2 uses A2S with challenge-response authentication as documented at: https://developer.valvesoftware.com/wiki/Server_queries

## Features

### 1. Real-time Server Query

Query your CS2 server to get:
- Server name and current map
- Player count and list of players
- Game version and platform
- VAC status and password protection
- Bot count

### 2. A2S Monitoring for Auto-Restart

Instead of using SSH process checks, you can now use A2S queries to monitor server health:
- More accurate server status (process might be running but server frozen)
- Less overhead (no SSH connection needed)
- Configurable failure threshold

**Configuration:**
- **Enable A2S Monitoring**: Toggle to use A2S instead of SSH for monitoring
- **Failure Threshold**: Number of consecutive A2S query failures before triggering auto-restart (1-10, default: 3)

### 3. Custom Query Address/Port

If your CS2 server is behind a firewall or uses port forwarding, you can configure:
- **A2S Query Host**: Custom hostname/IP for A2S queries (defaults to server host)
- **A2S Query Port**: Custom port for A2S queries (defaults to game port)

## How to Use

### Querying Server Information

1. Navigate to your server's detail page (`/servers-ui/{server_id}`)
2. Find the **"A2S Server Query"** card
3. Click **"Query Now"** to fetch real-time server information
4. View server details, player list, and status

### Configuring A2S Monitoring

1. Navigate to your server's detail page
2. In the **"A2S Server Query"** card, click **"Configure"**
3. Set your A2S query host/port (optional, uses defaults if not set)
4. Enable **"Enable A2S Monitoring for Auto-Restart"**
5. Adjust the **failure threshold** (recommended: 3-5)
6. Save configuration

**Important:** A2S monitoring requires **Panel Monitoring** to be enabled in the "Panel Monitoring & Auto-Restart" section.

### How A2S Monitoring Works

When A2S monitoring is enabled:
1. The panel performs A2S queries at the configured interval (e.g., every 60 seconds)
2. If a query fails, the failure counter increments
3. If failures reach the threshold (e.g., 3 consecutive failures), the server is marked as down
4. If auto-restart is enabled, the server will be automatically restarted
5. On successful query, the failure counter resets to 0

## Bug Fixes

### CS2 MaxPlayers Parameter Fix

Fixed a bug where the server start command used `+maxplayers` instead of the correct CS2 parameter `-maxplayers`. This ensures the max players setting is correctly applied when starting your server.

## Technical Details

- **Library**: Uses `python-a2s` library for A2S protocol implementation
- **CS2 Challenge Support**: Fully supports CS2's challenge-response authentication
- **Async Implementation**: All queries are performed asynchronously for better performance
- **Error Handling**: Gracefully handles timeouts and connection errors

## Monitoring Configuration Examples

### Example 1: Basic A2S Monitoring
```
Panel Monitoring: Enabled
Monitor Interval: 60 seconds
A2S Monitoring: Enabled
A2S Failure Threshold: 3
```
This will check the server every 60 seconds and restart if 3 consecutive A2S queries fail.

### Example 2: Strict Monitoring
```
Panel Monitoring: Enabled
Monitor Interval: 30 seconds
A2S Monitoring: Enabled
A2S Failure Threshold: 5
```
More frequent checks with a higher threshold to avoid false positives.

### Example 3: Behind Firewall
```
Panel Monitoring: Enabled
A2S Query Host: public-ip.example.com
A2S Query Port: 27015
A2S Monitoring: Enabled
A2S Failure Threshold: 3
```
Use public IP for queries when server is behind NAT/firewall.

## API Endpoints

- `GET /servers/{server_id}/a2s-info` - Query server via A2S and return information

## Database Schema

New fields added to `servers` table:
- `a2s_query_host` (VARCHAR 255, nullable) - Custom A2S query host
- `a2s_query_port` (INT, nullable) - Custom A2S query port
- `enable_a2s_monitoring` (BOOLEAN, default: false) - Enable A2S monitoring
- `a2s_failure_threshold` (INT, default: 3) - Consecutive failures before restart

## Compatibility

- Requires CS2 dedicated server
- Server must allow A2S queries (default enabled in CS2)
- Firewall must allow UDP queries on the game port
