# LGSM-Style Server Configuration Guide

## Overview

The CS2 Server Manager now supports comprehensive LGSM (LinuxGSM) style configuration, allowing you to fully customize server startup parameters just like LinuxGSM does.

## New Configuration Fields

### Basic Server Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_name` | string | "CS2 Server" | Server hostname displayed in server browser |
| `default_map` | string | "de_dust2" | Map to load on server start |
| `max_players` | integer | 32 | Maximum number of players (1-64) |
| `tickrate` | integer | 128 | Server tickrate (64 or 128) |
| `game_mode` | string | "competitive" | Game mode |
| `game_type` | string | "0" | Game type |

### Security Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_password` | string | null | Server password (players must enter to join) |
| `rcon_password` | string | null | RCON password for remote administration |

### Network Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `game_port` | integer | 27015 | Main game server port |
| `client_port` | integer | null | Client port (auto: game_port + 1) |
| `ip_address` | string | null | Bind to specific IP address |

### SourceTV Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tv_enable` | boolean | false | Enable SourceTV (GOTV) |
| `tv_port` | integer | null | SourceTV port |

### Advanced Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `additional_parameters` | string | null | Custom command-line parameters |

## Command Line Generation

The server startup command is automatically built using these parameters:

### Base Command
```bash
screen -dmS cs2server_{id} /path/to/cs2 -dedicated -port {game_port} +map {default_map} +maxplayers {max_players} -tickrate {tickrate} +hostname "{server_name}"
```

### With Optional Parameters
```bash
# If IP address is set:
-ip {ip_address}

# If client port is set (or auto-calculated):
+clientport {client_port}

# If server password is set:
+sv_password "{server_password}"

# If RCON password is set:
+rcon_password "{rcon_password}"

# Game mode and type:
+game_mode {game_mode} +game_type {game_type}

# If SourceTV is enabled:
+tv_enable 1 +tv_port {tv_port} +tv_name "GOTV"

# Additional parameters:
{additional_parameters}
```

## Usage Examples

### Example 1: Basic Competitive Server

```json
{
  "name": "My CS2 Server",
  "host": "192.168.1.100",
  "ssh_user": "cs2server",
  "auth_type": "password",
  "ssh_password": "your_password",
  "game_port": 27015,
  "game_directory": "/home/cs2server/cs2",
  
  "server_name": "My Competitive Server",
  "default_map": "de_mirage",
  "max_players": 10,
  "tickrate": 128,
  "game_mode": "competitive",
  "game_type": "0",
  "rcon_password": "secure_rcon_pass"
}
```

**Generated command:**
```bash
screen -dmS cs2server_1 /home/cs2server/cs2/game/bin/linuxsteamrt64/cs2 \
  -dedicated \
  -port 27015 \
  +map de_mirage \
  +maxplayers 10 \
  -tickrate 128 \
  +hostname "My Competitive Server" \
  +clientport 27016 \
  +rcon_password "secure_rcon_pass" \
  +game_mode competitive \
  +game_type 0
```

### Example 2: Casual Server with SourceTV

```json
{
  "server_name": "Casual Fun Server",
  "default_map": "de_dust2",
  "max_players": 20,
  "tickrate": 64,
  "game_mode": "casual",
  "game_type": "0",
  "server_password": "friends_only",
  "rcon_password": "admin123",
  "tv_enable": true,
  "tv_port": 27020
}
```

**Generated command:**
```bash
screen -dmS cs2server_2 /home/cs2server/cs2/game/bin/linuxsteamrt64/cs2 \
  -dedicated \
  -port 27015 \
  +map de_dust2 \
  +maxplayers 20 \
  -tickrate 64 \
  +hostname "Casual Fun Server" \
  +clientport 27016 \
  +sv_password "friends_only" \
  +rcon_password "admin123" \
  +game_mode casual \
  +game_type 0 \
  +tv_enable 1 \
  +tv_port 27020 \
  +tv_name "GOTV"
```

### Example 3: Custom Parameters

```json
{
  "server_name": "Advanced Server",
  "default_map": "de_inferno",
  "max_players": 32,
  "tickrate": 128,
  "rcon_password": "admin",
  "additional_parameters": "+sv_cheats 0 +mp_autoteambalance 1 +mp_limitteams 1"
}
```

**Generated command:**
```bash
screen -dmS cs2server_3 /home/cs2server/cs2/game/bin/linuxsteamrt64/cs2 \
  -dedicated \
  -port 27015 \
  +map de_inferno \
  +maxplayers 32 \
  -tickrate 128 \
  +hostname "Advanced Server" \
  +clientport 27016 \
  +rcon_password "admin" \
  +game_mode competitive \
  +game_type 0 \
  +sv_cheats 0 +mp_autoteambalance 1 +mp_limitteams 1
```

## Common Game Modes

| Game Mode | game_mode | game_type | Description |
|-----------|-----------|-----------|-------------|
| Competitive | competitive | 0 | 5v5 competitive matchmaking |
| Casual | casual | 0 | Casual gameplay |
| Deathmatch | deathmatch | 1 | Free-for-all deathmatch |
| Arms Race | armsrace | 1 | Gun game progression |
| Demolition | demolition | 1 | Bomb defusal with weapon progression |

## Popular Maps

- `de_dust2` - Classic bomb defusal map
- `de_mirage` - Competitive favorite
- `de_inferno` - Tight corridors and strategic play
- `de_nuke` - Vertical gameplay
- `de_ancient` - Ancient ruins theme
- `de_anubis` - Egyptian theme
- `de_vertigo` - High-rise construction site
- `cs_office` - Hostage rescue
- `cs_italy` - Hostage rescue

## API Usage

### Creating a Server with LGSM Configuration

```bash
curl -X POST "http://localhost:8000/servers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "My Server",
    "host": "192.168.1.100",
    "ssh_user": "cs2server",
    "auth_type": "password",
    "ssh_password": "password",
    "game_port": 27015,
    "game_directory": "/home/cs2server/cs2",
    
    "server_name": "My Awesome CS2 Server",
    "default_map": "de_mirage",
    "max_players": 10,
    "tickrate": 128,
    "game_mode": "competitive",
    "game_type": "0",
    "rcon_password": "secure_password",
    "tv_enable": true,
    "tv_port": 27020
  }'
```

### Updating Server Configuration

```bash
curl -X PUT "http://localhost:8000/servers/1" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "server_name": "Updated Server Name",
    "default_map": "de_dust2",
    "max_players": 16,
    "additional_parameters": "+sv_hibernate_when_empty 1"
  }'
```

## Database Migration

For existing installations, run the migration script:

```bash
mysql -u your_user -p your_database < migrations/add_lgsm_config_fields.sql
```

Or if using the application's database connection, the new fields will be automatically created on next startup.

## Troubleshooting

### Server Won't Start

1. **Check ports**: Ensure `game_port`, `client_port`, and `tv_port` are not in use
2. **Verify map**: Make sure `default_map` is a valid installed map
3. **Review logs**: Check server logs for specific error messages
4. **Test parameters**: Try removing `additional_parameters` to isolate issues

### Performance Issues

1. **Tickrate**: Lower tickrate (64) uses less CPU than higher (128)
2. **Max players**: Reduce `max_players` if server struggles
3. **SourceTV**: Disable `tv_enable` if not needed (uses extra resources)

## Best Practices

1. **RCON Password**: Always set a strong `rcon_password` for security
2. **Server Password**: Use `server_password` for private servers
3. **Tickrate**: Use 128 for competitive, 64 for casual
4. **Client Port**: Let it auto-calculate (game_port + 1) unless you have specific needs
5. **Custom Parameters**: Test `additional_parameters` thoroughly before production use

## References

- [LinuxGSM CS2 Documentation](https://linuxgsm.com/servers/cs2server/)
- [CS2 Dedicated Server Documentation](https://developer.valvesoftware.com/wiki/Counter-Strike_2/Dedicated_Servers)
- [CS2 Server Commands](https://developer.valvesoftware.com/wiki/List_of_CS2_Server_Commands)
