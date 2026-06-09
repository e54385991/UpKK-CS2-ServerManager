# Plugin Market Feature Documentation

## Overview
The Plugin Market module provides a centralized marketplace for CS2 server plugins, allowing users to easily browse, search, and install plugins with one click. Administrators can add plugins to the market by simply providing a GitHub repository URL, and the system automatically fetches plugin information.

## Features

### For End Users
- **Browse Plugins**: View all available plugins with pagination
- **Search**: Search plugins by name, author, or description
- **Filter by Category**: Filter plugins by category (Game Mode, Entertainment, Utility, Admin, Performance, Library, Other)
- **One-Click Installation**: Install plugins directly to your server without manual download
- **Version Selection**: Choose specific plugin versions/releases to install (NEW)
- **Selective Extraction**: Advanced option to exclude specific files during installation for safe upgrades (NEW)
- **Automatic Dependency Installation**: When installing a plugin, all required dependencies are automatically installed first
- **Plugin Information**: View detailed information including:
  - Plugin name and description
  - Author information
  - Version number
  - Download and install statistics
  - Category and tags
  - Recommended badge for featured plugins
  - Dependency indicator (if plugin requires other plugins)

### For Administrators
- **Add Plugins**: Add new plugins by providing GitHub repository URL
- **Auto-Fill**: Automatically fetch repository name, description, and author from GitHub
- **Edit Plugins**: Update plugin information, category, tags, and recommendation status
- **Delete Plugins**: Remove plugins from the market
- **Manage Categories**: Plugins are organized into predefined categories
- **Specify Dependencies**: Select required plugins from the market that must be installed first
- **Dependency Validation**: System validates all dependencies exist before saving

## API Endpoints

### Public Endpoints (Authenticated Users)

#### GET `/api/plugin-market/plugins`
List plugins with pagination, filtering, and search.

**Query Parameters:**
- `page` (int, default: 1): Page number
- `page_size` (int, default: 20, max: 100): Items per page
- `category` (string, optional): Filter by category
- `search` (string, optional): Search query for name, author, or description

**Response:**
```json
{
  "success": true,
  "plugins": [
    {
      "id": 1,
      "github_url": "https://github.com/owner/repo",
      "title": "Example Plugin",
      "description": "Plugin description...",
      "author": "author_name",
      "version": "1.0.0",
      "category": "utility",
      "tags": "tag1, tag2",
      "is_recommended": false,
      "icon_url": "https://...",
      "download_count": 100,
      "install_count": 50,
      "created_at": "2024-01-01T00:00:00",
      "updated_at": "2024-01-01T00:00:00"
    }
  ],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

#### GET `/api/plugin-market/plugins/{plugin_id}`
Get details of a specific plugin.

#### GET `/api/plugin-market/plugins/{plugin_id}/releases`
Fetch available releases (versions) for a plugin.

**Query Parameters:**
- `server_id` (int, optional): Server ID to use server's GitHub proxy
- `count` (int, default: 5, max: 10): Number of releases to fetch

**Response:**
```json
{
  "success": true,
  "releases": [
    {
      "tag_name": "v1.2.0",
      "name": "Version 1.2.0",
      "published_at": "2024-01-01T00:00:00Z",
      "prerelease": false,
      "assets": [
        {
          "name": "plugin-linux.zip",
          "browser_download_url": "https://github.com/.../releases/download/v1.2.0/plugin-linux.zip",
          "size": 1024000,
          "content_type": "application/zip"
        }
      ]
    }
  ],
  "repo_owner": "owner",
  "repo_name": "repo"
}
```

#### GET `/api/plugin-market/plugins/{plugin_id}/analyze-archive`
Analyze a plugin archive to show file structure (for selective extraction).

**Query Parameters:**
- `server_id` (int, required): Server ID for SSH connection
- `download_url` (string, optional): Specific release download URL (uses latest if not provided)

**Response:**
```json
{
  "success": true,
  "has_addons_dir": true,
  "root_dirs": ["addons", "cfg"],
  "all_dirs": ["addons", "addons/counterstrikesharp", "addons/counterstrikesharp/configs", "cfg"],
  "all_files": [
    {
      "path": "addons/counterstrikesharp/plugins/MyPlugin.dll",
      "is_dir": false,
      "size": 51200
    },
    {
      "path": "cfg/MyPlugin/config.json",
      "is_dir": false,
      "size": 2048
    }
  ],
  "archive_type": "zip"
}
```

#### POST `/api/plugin-market/plugins/{plugin_id}/install`
Install a plugin to a server.

**Query Parameters:**
- `server_id` (int, required): Server ID to install on
- `download_url` (string, optional): Specific release download URL (uses latest if not provided)
- `exclude_dirs` (list[string], optional): Directories to exclude from installation (deprecated, use exclude_files)
- `exclude_files` (list[string], optional): Files to exclude from installation
- `install_dependencies` (bool, default: true): Whether to automatically install dependencies

**Response:**
```json
{
  "success": true,
  "message": "Plugin installed successfully!",
  "installed_files": 42
}
```

**Note**: When `exclude_files` or `exclude_dirs` is specified, dependencies are NOT automatically installed unless explicitly set via `install_dependencies=true`.

#### GET `/api/plugin-market/categories`
Get list of available plugin categories.

### Admin-Only Endpoints

#### POST `/api/plugin-market/plugins`
Add a new plugin to the market.

**Request Body:**
```json
{
  "github_url": "https://github.com/owner/repo",
  "title": "Plugin Title",
  "description": "Plugin description",
  "author": "Author Name",
  "version": "1.0.0",
  "category": "utility",
  "tags": "tag1, tag2",
  "is_recommended": false,
  "icon_url": "https://..."
}
```

Note: If `title`, `description`, or `author` are not provided, they will be auto-fetched from GitHub.

#### PUT `/api/plugin-market/plugins/{plugin_id}`
Update a plugin in the market.

#### DELETE `/api/plugin-market/plugins/{plugin_id}`
Delete a plugin from the market.

#### POST `/api/plugin-market/fetch-repo-info`
Fetch repository information from GitHub (helper for auto-filling).

**Query Parameters:**
- `github_url` (string, required): GitHub repository URL

## Database Schema

### Table: `market_plugins`

| Column | Type | Description |
|--------|------|-------------|
| id | INT (PK) | Auto-increment primary key |
| github_url | VARCHAR(500) UNIQUE | GitHub repository URL |
| title | VARCHAR(255) | Plugin title |
| description | TEXT | Plugin description |
| author | VARCHAR(255) | Plugin author |
| version | VARCHAR(50) | Plugin version |
| category | ENUM | Plugin category |
| tags | TEXT | Comma-separated tags |
| is_recommended | BOOLEAN | Whether plugin is recommended |
| icon_url | VARCHAR(500) | Plugin icon URL |
| download_count | INT | Number of downloads |
| install_count | INT | Number of successful installs |
| created_at | TIMESTAMP | Creation timestamp |
| updated_at | TIMESTAMP | Last update timestamp |

**Indexes:**
- Primary key on `id`
- Unique index on `github_url`
- Index on `title`

## Categories

The following categories are available:

- **game_mode**: Game modes and gameplay modifications
- **entertainment**: Fun and entertainment plugins
- **utility**: Utility tools and helpers
- **admin**: Administration and moderation tools
- **performance**: Performance optimization plugins
- **library**: Libraries and dependencies
- **other**: Other plugins

## Frontend Implementation

The plugin market frontend is implemented in `/templates/plugin_market.html` and includes:

1. **Filter Section**: Category dropdown and search input
2. **Plugin Cards**: Display plugin information with install button
3. **Pagination**: Navigate through pages of plugins
4. **Admin Panel**: Add plugin modal (only visible to admins)
5. **Install Modal**: Select server and install plugin

### Navigation

The plugin market is accessible from the main navigation menu:
- Icon: Shopping bag (`bi-shop`)
- Label: "Plugin Market"
- Route: `/plugin-market`

## Installation Flow

### Standard Installation Flow
1. User browses/searches for a plugin in the market
2. User clicks "Install" button on desired plugin
3. Modal appears with server selection dropdown
4. User selects a server
5. Version selection dropdown loads with available releases
6. User optionally selects a specific version (latest is default)
7. User clicks "Install" button
8. System fetches the selected (or latest) release from GitHub
9. System finds suitable release asset (Linux-compatible archive)
10. System reuses existing GitHub plugin installation logic:
    - Downloads archive to server (or via panel proxy)
    - Extracts and verifies plugin structure
    - Installs to CS2 server directory
    - Reports installation status via WebSocket
11. Installation and download counts are automatically incremented

### Advanced Installation Flow (Selective Extraction)
This mode is useful for upgrading plugins while preserving configuration files.

1. User browses/searches for a plugin in the market
2. User clicks "Install" button on desired plugin
3. Modal appears with server selection dropdown
4. User selects a server
5. Version selection dropdown loads with available releases
6. User optionally selects a specific version
7. User enables "Advanced Options (For Updates/Upgrades)" checkbox
8. Advanced options panel appears with warning about dependencies
9. User clicks "Analyze Plugin Package" button
10. System downloads and analyzes the archive structure
11. File tree with checkboxes appears, showing individual files with sizes
12. User selects files to EXCLUDE (e.g., config files)
13. User clicks "Install" button
14. System installs plugin WITHOUT installing dependencies automatically
15. System excludes selected files during extraction
16. Installation completes with clear messaging

**Important**: In advanced mode:
- Dependencies are NOT automatically installed
- User receives clear warning about this behavior
- Useful for updating plugins without overwriting configs
- Dependencies must be installed manually if needed

## Auto-Fill Feature

When administrators add a plugin, they can use the "Auto-fill from GitHub" button to:
1. Fetch repository information from GitHub API
2. Auto-populate title (from repo name)
3. Auto-populate description (first 200 characters of README)
4. Auto-populate author (from repository owner)

This significantly simplifies the plugin addition process.

## Integration with Existing Features

The plugin market integrates seamlessly with the existing GitHub plugin installation system:
- Uses the same `install_github_plugin` function
- Supports both direct download and panel proxy modes
- Respects server-level GitHub proxy settings
- Provides WebSocket progress updates during installation
- Supports file and directory exclusions for updates

## Security Considerations

- All plugin management endpoints require authentication
- Admin-only endpoints require admin privileges
- Plugin GitHub URLs are validated before processing
- Installation is limited to user's own servers
- Only Linux-compatible release assets are considered for installation

## Future Enhancements

Potential future improvements:
- Plugin ratings and reviews
- Version history tracking
- Automatic plugin updates notification
- Plugin screenshots gallery
- Integration with plugin documentation
- Community-submitted plugins with approval workflow

## Recent Updates (December 2024)

### Version Selection
Users can now select specific versions/releases when installing plugins:
- Dropdown shows up to 10 most recent releases
- Latest version is selected by default
- Pre-release versions are marked with [Pre-release] tag
- Supports all release types (stable and pre-release)

### Selective Extraction/Upgrade Mode
Advanced installation option for safe plugin updates:
- Analyze plugin package before installation
- View complete directory structure
- Select specific directories to exclude from extraction
- Preserve configuration files during upgrades
- Clear warning about dependency installation behavior
- Useful for updating plugins without losing custom configs
