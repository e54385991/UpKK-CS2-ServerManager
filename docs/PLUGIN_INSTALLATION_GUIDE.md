# Plugin Framework Installation Guide

This guide explains how to install and manage plugin frameworks (Metamod:Source and CounterStrikeSharp) for your CS2 servers using the CS2 Server Manager.

## Overview

The CS2 Server Manager supports automatic installation of popular plugin frameworks:

- **Metamod:Source 2.0**: A plugin loader for Source 2 engine, required for most CS2 server plugins
- **CounterStrikeSharp**: A modern C# plugin framework for CS2, built on top of Metamod

## Features

✅ **Automatic Installation**: One-click installation of plugin frameworks  
✅ **Latest Versions**: Always fetches the latest releases from official sources  
✅ **Batch Installation**: Install plugins on multiple servers simultaneously  
✅ **Update Support**: Easy updates to keep plugins current  
✅ **Real-time Progress**: WebSocket-based live installation logs  
✅ **Dependency Management**: Auto-installs Metamod when installing CounterStrikeSharp  

## Prerequisites

Before installing plugin frameworks, ensure:

1. Your CS2 server is deployed and installed
2. The `unzip` utility is installed on the target server (required for CounterStrikeSharp)
3. You have sufficient disk space for plugin files

## Installation Methods

### Method 1: Web UI - Single Server

1. Navigate to **Servers** page
2. Click on a server to view details
3. Scroll to the **Plugin Framework Management** section
4. Choose your plugin:
   - Click **Install Metamod** to install Metamod:Source
   - Click **Install CounterStrikeSharp** to install CounterStrikeSharp
5. Watch the real-time installation logs
6. After installation completes, **restart your server**

### Method 2: Web UI - Batch Installation

For installing plugins on multiple servers at once:

1. Navigate to **Servers** page
2. **Select** the servers you want to install plugins on (use checkboxes)
3. Click **Install Plugins** in the bulk actions bar
4. Select which plugins to install:
   - Metamod:Source
   - CounterStrikeSharp
5. Confirm the installation
6. Monitor progress in the progress bar
7. Review the installation results
8. **Restart all servers** for changes to take effect

### Method 3: API

#### Install Metamod on a Single Server

```bash
curl -X POST "http://localhost:8000/servers/{server_id}/actions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "install_metamod"}'
```

#### Install CounterStrikeSharp on a Single Server

```bash
curl -X POST "http://localhost:8000/servers/{server_id}/actions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "install_counterstrikesharp"}'
```

#### Batch Install on Multiple Servers

```bash
curl -X POST "http://localhost:8000/servers/batch-install-plugins" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "server_ids": [1, 2, 3],
    "plugins": ["metamod", "counterstrikesharp"]
  }'
```

## What Gets Installed

### Metamod:Source

When you install Metamod:Source, the system:

1. Downloads the latest Metamod:Source 2.0 build from `mms.alliedmods.net`
2. Extracts to `{game_directory}/cs2/game/csgo/addons/metamod`
3. Modifies `gameinfo.gi` to load Metamod (adds `Game csgo/addons/metamod`)
4. Creates a backup of `gameinfo.gi` before modification

**Installation Path**: `{game_directory}/cs2/game/csgo/addons/metamod`

### CounterStrikeSharp

When you install CounterStrikeSharp, the system:

1. Checks if Metamod is installed (installs it if missing)
2. Fetches the latest release from GitHub
3. Downloads the "with-runtime" Linux build
4. Extracts to `{game_directory}/cs2/game/csgo/addons/counterstrikesharp`

**Installation Path**: `{game_directory}/cs2/game/csgo/addons/counterstrikesharp`

## Updating Plugins

To update to the latest version:

### Via Web UI

1. Go to server detail page
2. In the **Plugin Framework Management** section, click:
   - **Update Metamod** to update Metamod:Source
   - **Update CounterStrikeSharp** to update CounterStrikeSharp
3. Restart your server after update

### Via API

```bash
# Update Metamod
curl -X POST "http://localhost:8000/servers/{server_id}/actions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "update_metamod"}'

# Update CounterStrikeSharp
curl -X POST "http://localhost:8000/servers/{server_id}/actions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "update_counterstrikesharp"}'
```

## Verifying Installation

After installing plugins and restarting your server:

1. Connect to your server console
2. Run these commands:
   - `meta list` - Should show CounterStrikeSharp plugin
   - `css_plugins list` - Should list loaded CounterStrikeSharp plugins

Expected output for `meta list`:
```
Listing 1 plugin:
  [01] CounterStrikeSharp (version) by Roflmuffin
```

## Important Notes

### gameinfo.gi File

⚠️ **Important**: After every CS2 server update by Valve, the `gameinfo.gi` file gets overwritten. You may need to:
- Re-install Metamod (or manually re-add the line)
- Or use the "Update Metamod" function which re-applies the modification

### Server Restart Required

🔄 **Always restart your CS2 server** after installing or updating plugins. The plugins won't load until the server restarts.

### Dependency Chain

CounterStrikeSharp requires Metamod:Source. The installation system automatically:
- Checks for Metamod before installing CounterStrikeSharp
- Installs Metamod if it's missing
- Continues with CounterStrikeSharp installation

## Troubleshooting

### "unzip command not found"

CounterStrikeSharp requires `unzip` to extract the archive. Install it on your server:

```bash
sudo apt-get update
sudo apt-get install unzip
```

### Metamod not loading

1. Check if the line exists in `gameinfo.gi`:
   ```
   Game    csgo/addons/metamod
   ```
2. Verify the line is placed correctly (after `Game_LowViolence csgo_lv`)
3. Restart the server
4. Check server logs for errors

### CounterStrikeSharp not showing in meta list

1. Verify Metamod is loaded (`meta list` in console)
2. Check if files exist: `{game_directory}/cs2/game/csgo/addons/counterstrikesharp`
3. Verify file permissions (should be readable by SSH user)
4. Restart the server
5. Check server console for loading errors

### After server update, plugins stop working

This is normal. After Valve updates CS2:
1. The `gameinfo.gi` file gets overwritten
2. Use the **Update Metamod** function to restore the modification
3. Or manually re-add: `Game csgo/addons/metamod` to `gameinfo.gi`
4. Restart the server

## Installing Additional Plugins

After installing the frameworks, you can install additional plugins:

1. Upload plugin files to:
   - For Metamod plugins: `{game_directory}/cs2/game/csgo/addons/metamod/plugins/`
   - For CounterStrikeSharp plugins: `{game_directory}/cs2/game/csgo/addons/counterstrikesharp/plugins/`
2. Restart the server
3. Verify with `meta list` or `css_plugins list`

## Plugin Resources

- **Metamod:Source**: https://www.sourcemm.net/
- **CounterStrikeSharp**: https://docs.cssharp.dev/
- **CS2 Plugin Database**: https://github.com/roflmuffin/CounterStrikeSharp
- **AlliedModders Forums**: https://forums.alliedmods.net/

## API Actions Reference

| Action | Description |
|--------|-------------|
| `install_metamod` | Install Metamod:Source 2.0 |
| `install_counterstrikesharp` | Install CounterStrikeSharp (auto-installs Metamod if needed) |
| `update_metamod` | Update Metamod to latest version |
| `update_counterstrikesharp` | Update CounterStrikeSharp to latest version |
| `backup_plugins` | Backup plugins (addons, cfg folders and gameinfo.gi file) |

## Backing Up Plugins

The plugin backup feature allows you to create timestamped backups of your server's plugin configuration and files.

### What Gets Backed Up

When you create a backup, the system backs up the following from `{game_directory}/cs2/game/csgo/`:
- `addons/` folder (contains all installed plugins)
- `cfg/` folder (contains configuration files)
- `gameinfo.gi` file (contains game configuration including Metamod loading)

### Backup Location

Backups are saved to: `{game_directory}/backups/YYYY-MM-DD-HHMMSS.tar.gz`

Example: If your game directory is `/home/cs2server/cs2kz`, backups will be saved to `/home/cs2server/cs2kz/backups/2025-11-24-143025.tar.gz`

### Creating a Backup via Web UI

1. Navigate to **Servers** page
2. Click on a server to view details
3. Scroll to the **Plugin Framework Management** section
4. Click **Backup Plugins**
5. Watch the real-time backup progress
6. The backup file path will be displayed upon completion

### Creating a Backup via API

```bash
curl -X POST "http://localhost:8000/servers/{server_id}/actions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "backup_plugins"}'
```

### Smart Backup

The backup system intelligently:
- Checks which items exist before backing up
- Only backs up items that are present
- Warns if any items are missing
- Displays backup file size in human-readable format
- Provides real-time progress updates via WebSocket

### Restoring from Backup

To restore from a backup:

1. Connect to your server via SSH
2. Navigate to the backup directory:
   ```bash
   cd /home/cs2server/cs2kz/backups
   ```
3. Extract the backup:
   ```bash
   tar -xzf 2025-11-24-143025.tar.gz -C /home/cs2server/cs2kz/cs2/game/csgo/
   ```
4. Restart your CS2 server

**Note**: Replace the paths with your actual server paths.

### Best Practices

- **Before Updates**: Always create a backup before updating plugins or the CS2 server
- **Regular Backups**: Create backups regularly, especially before major changes
- **Test Backups**: Periodically verify that your backups can be restored successfully
- **Storage Management**: Old backups are not automatically deleted; manage disk space manually

## Support

If you encounter issues:

1. Check the deployment logs in the Web UI
2. Verify server prerequisites (unzip, disk space)
3. Check server console for error messages
4. Consult the official documentation for the respective plugin framework
5. Create an issue on the GitHub repository

## License

This installation feature is part of the CS2 Server Manager project, licensed under MIT License.
