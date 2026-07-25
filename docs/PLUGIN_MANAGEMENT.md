# CounterStrikeSharp Plugin Management System

## Overview

This plugin management system provides a comprehensive solution for managing CounterStrikeSharp plugins through the CS2 Server Manager web interface.

## Features

- **Plugin Catalog**: Browse available plugins with detailed information
- **Category Filtering**: Filter plugins by category (Utility, Gameplay, Admin, Chat, Statistics, Cosmetic, Other)
- **Pagination**: Efficiently browse large plugin catalogs with pagination support
- **Dependency Management**: Automatically install plugin dependencies
- **Custom Download URLs**: Override default download URLs for custom builds
- **Configuration Support**: Provide JSON configuration for plugins that require it
- **Installation Tracking**: Track which plugins are installed on each server
- **One-Click Install/Uninstall**: Simple interface for managing plugins

## Database Schema

### Plugin Table

Stores the catalog of available plugins:

- `id`: Primary key
- `name`: Unique plugin identifier
- `display_name`: User-friendly name
- `description`: Plugin description
- `category`: Plugin category (enum)
- `version`: Current version
- `download_url`: Default download URL (tar.gz archive)
- `author`: Plugin author
- `homepage`: Plugin homepage URL
- `dependencies`: JSON array of plugin IDs that this plugin depends on
- `install_path`: Installation path relative to game directory
- `config_required`: Whether the plugin requires configuration
- `enabled`: Whether the plugin is available in the catalog

### InstalledPlugin Table

Tracks which plugins are installed on which servers:

- `id`: Primary key
- `server_id`: Foreign key to servers table
- `plugin_id`: Foreign key to plugins table
- `version`: Installed version
- `custom_download_url`: Custom download URL if used
- `config_data`: JSON configuration data
- `installed_at`: Installation timestamp

## API Endpoints

### GET /api/plugins/categories
Get all available plugin categories.

**Response**: Array of category objects with `value` and `label`

### GET /api/plugins
List available plugins with pagination and filtering.

**Query Parameters**:
- `category` (optional): Filter by category
- `page` (default: 1): Page number
- `page_size` (default: 20, max: 100): Items per page

**Response**: 
```json
{
  "plugins": [...],
  "total": 50,
  "page": 1,
  "page_size": 20,
  "total_pages": 3
}
```

### GET /api/plugins/{plugin_id}
Get details of a specific plugin.

### POST /api/plugins
Create a new plugin in the catalog (admin only).

**Request Body**: PluginCreate schema

### GET /api/plugins/servers/{server_id}/installed
Get all plugins installed on a server.

### POST /api/plugins/servers/{server_id}/install
Install a plugin on a server.

**Request Body**:
```json
{
  "plugin_id": 1,
  "custom_download_url": "https://...", // optional
  "config_data": "{\"key\": \"value\"}" // optional JSON string
}
```

### DELETE /api/plugins/servers/{server_id}/installed/{installed_plugin_id}
Uninstall a plugin from a server.

## UI Components

### Plugins Tab

The plugins tab is integrated into the server detail page and includes:

1. **Category Sidebar**: Filter plugins by category with plugin counts
2. **Installed Plugins Sidebar**: View and manage installed plugins
3. **Plugin Grid**: Browse available plugins with cards showing:
   - Plugin name and description
   - Category badge
   - Version and author
   - Dependency indicators
   - Install/installed status
   - Homepage link
4. **Pagination Controls**: Navigate through pages of plugins
5. **Install Modal**: Configure installation with custom URL and config options

## Installation Process

When a plugin is installed:

1. Check if the plugin is already installed
2. Resolve and install dependencies first (if any)
3. Connect to the server via SSH
4. Create the installation directory
5. Download the plugin archive from the specified URL
6. Extract the archive to the correct location
7. Clean up temporary files
8. Record the installation in the database

## Plugin Archive Format

Plugins should be distributed as `.tar.gz` archives with the following structure:

```
plugin_name.tar.gz
├── plugin_name/           # Directory must match plugin.name (sanitized: alphanumeric + dash/underscore)
│   ├── PluginName.dll
│   ├── config.json (optional)
│   └── lang/ (optional)
│       └── en.json
```

**Important**: The top-level directory in the archive should match the plugin's `name` field in the database (with only alphanumeric characters, dashes, and underscores). This is required for proper cleanup during uninstallation.

The archive will be extracted to:
```
{game_directory}/game/csgo/{install_path}/
```

Default install path: `addons/counterstrikesharp/plugins`

Full installation path example:
```
/home/cs2server/cs2/game/csgo/addons/counterstrikesharp/plugins/plugin_name/
```

During uninstallation, the system will remove:
```
{game_directory}/game/csgo/{install_path}/{sanitized_plugin_name}/
```

## Adding Sample Plugins

To populate the database with sample plugins for testing:

```bash
cd /home/runner/work/UpKK-CS2-ServerManager/UpKK-CS2-ServerManager
python scripts/populate_plugins.py
```

This script adds 17 sample plugins across all categories with realistic metadata.

## Development

### Adding New Categories

1. Add the category to `PluginCategory` enum in `modules/models.py`
2. Update the category icon and color mappings in `scripts.html`:
   - `getCategoryIcon()`: Bootstrap Icons class
   - `getCategoryColor()`: Bootstrap color class

### Creating Custom Plugins

To add a custom plugin to the catalog:

1. Use the API endpoint `POST /api/plugins` (admin only)
2. Or directly add to the database using the Plugin model

Example:
```python
plugin = Plugin(
    name="my_plugin",
    display_name="My Plugin",
    description="Does something cool",
    category=PluginCategory.UTILITY,
    version="1.0.0",
    download_url="https://github.com/user/plugin/releases/download/v1.0.0/plugin.tar.gz",
    author="Your Name",
    homepage="https://github.com/user/plugin",
    install_path="addons/counterstrikesharp/plugins",
    config_required=False
)
```

## Security Considerations

- Plugin installations run with the same SSH credentials as the server
- Download URLs should use HTTPS to prevent man-in-the-middle attacks
- Plugin archives are extracted with tar, which could potentially overwrite files if malicious
- Only administrators can add new plugins to the catalog
- Users can only install plugins on servers they own

## Future Enhancements

Potential future improvements:

- Plugin version update detection
- Automatic plugin updates
- Plugin conflict detection
- Plugin ratings and reviews
- Search functionality
- Direct GitHub integration for popular plugins
- Plugin configuration editor UI
- Backup before installation
- Rollback functionality
