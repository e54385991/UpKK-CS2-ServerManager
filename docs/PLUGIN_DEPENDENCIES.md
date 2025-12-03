# Plugin Dependencies Feature Documentation

## Overview
The plugin dependencies feature allows administrators to specify which plugins are required for a plugin to function properly. When users install a plugin with dependencies, the system automatically installs all required plugins in the correct order.

## Features

### For Administrators
When adding or editing a plugin in the market, administrators can:
- Select multiple dependencies from already published plugins in the market
- Dependencies are stored as comma-separated plugin IDs
- The system validates that all dependency IDs exist in the database
- Dependencies are displayed in a multi-select dropdown (hold Ctrl/Cmd to select multiple)

### For End Users
When installing a plugin with dependencies:
1. System checks if the plugin has dependencies
2. Automatically installs all dependencies first (in order)
3. Then installs the main plugin
4. Installation result shows which dependencies were installed
5. If a dependency fails, the process continues with remaining plugins
6. Users can optionally disable dependency installation via API parameter

## Database Schema

### Updated Fields
The `market_plugins` table now includes:
- `dependencies` (TEXT, nullable): Comma-separated list of plugin IDs

Example: `"1,5,12"` means plugin requires plugins with IDs 1, 5, and 12.

## API Changes

### POST /api/plugin-market/plugins
**Request body (new field):**
```json
{
  "github_url": "https://github.com/owner/repo",
  "title": "My Plugin",
  "dependencies": "1,5,12"
  // ... other fields
}
```

### PUT /api/plugin-market/plugins/{id}
**Request body (new field):**
```json
{
  "dependencies": "1,5,12"
  // ... other fields
}
```

### POST /api/plugin-market/plugins/{id}/install
**New query parameter:**
- `install_dependencies` (boolean, default: true) - Whether to automatically install dependencies

**Enhanced response:**
```json
{
  "success": true,
  "message": "Plugin installed successfully! (Dependencies also installed: Metamod, CounterStrikeSharp)",
  "installed_files": 42
}
```

## Frontend Changes

### Add Plugin Modal
New multi-select dropdown for dependencies:
- Lists all published plugins in the market
- Shows plugin title and ID for easy identification
- Supports multiple selection (Ctrl/Cmd + click)
- Help text explains the feature
- Selected plugins are automatically installed when users install this plugin

### Plugin Cards
- Shows icon if plugin has dependencies: "Has dependencies (will be auto-installed)"
- Helps users understand what will be installed

## Technical Implementation

### Dependency Validation
When creating or updating a plugin:
```python
# Parse dependency IDs
dep_ids = [dep.strip() for dep in request.dependencies.split(',') if dep.strip()]

# Validate each dependency exists
for dep_id in dep_ids:
    dep_plugin = await MarketPlugin.get_by_id(db, int(dep_id))
    if not dep_plugin:
        raise HTTPException(status_code=404, detail=f"Dependency plugin with ID {dep_id} not found")
```

### Recursive Installation
When installing a plugin with dependencies:
```python
# Install dependencies first
if install_dependencies and plugin.dependencies:
    dep_ids = [dep.strip() for dep in plugin.dependencies.split(',') if dep.strip()]
    for dep_id in dep_ids:
        # Recursively install dependency (without its own dependencies to avoid infinite loops)
        dep_result = await install_plugin(
            int(dep_id), 
            server_id, 
            exclude_dirs, 
            install_dependencies=False,  # Prevent infinite recursion
            db=db, 
            current_user=current_user
        )
```

### Infinite Loop Prevention
- When installing dependencies, `install_dependencies` is set to `False`
- This prevents dependencies from installing their own dependencies
- Creates a flat dependency tree (only one level deep)
- Avoids circular dependency issues

## Example Workflow

### Admin: Add Plugin with Dependencies
1. Click "Add Plugin" in admin panel
2. Fill in GitHub URL and click "Auto-fill"
3. Scroll to "Dependencies" section
4. Select required plugins from the multi-select dropdown (e.g., Metamod, CounterStrikeSharp)
5. Click "Add Plugin"
6. System validates dependencies and saves

### User: Install Plugin
1. Browse plugin market
2. Click "Install" on desired plugin
3. Select target server
4. Click "Install"
5. System automatically:
   - Downloads and installs Metamod
   - Downloads and installs CounterStrikeSharp
   - Downloads and installs the main plugin
   - Shows: "Plugin installed successfully! (Dependencies also installed: Metamod, CounterStrikeSharp)"

## Error Handling

### Invalid Dependency ID
```json
{
  "detail": "Invalid dependency ID: abc"
}
```

### Non-existent Dependency
```json
{
  "detail": "Dependency plugin with ID 999 not found"
}
```

### Partial Success
If dependencies install but main plugin fails:
```json
{
  "success": false,
  "message": "Failed to fetch latest release: timeout (Dependencies installed: Metamod, CounterStrikeSharp)"
}
```

## Migration

The database migration automatically adds the `dependencies` column:
```sql
ALTER TABLE market_plugins 
ADD COLUMN dependencies TEXT NULL
```

This runs automatically on application startup if the column doesn't exist.

## Best Practices

### For Administrators
1. Only add dependencies that are truly required
2. Test plugin installation to verify dependencies work correctly
3. Keep dependency lists minimal to reduce installation time
4. Document dependencies in plugin description
5. Prefer well-maintained plugins as dependencies

### For Users
1. Let auto-install handle dependencies (recommended)
2. Manually review what will be installed if concerned
3. Dependencies will be visible in installation confirmation

## Limitations

1. **Single-level dependencies**: Dependencies don't install their own dependencies (prevents infinite loops)
2. **No version constraints**: System installs latest version of dependencies
3. **No dependency conflict resolution**: If two plugins require different versions, latest version is installed
4. **No uninstall cascade**: Removing a plugin doesn't remove its dependencies

## Future Enhancements

Potential improvements:
- Multi-level dependency resolution with cycle detection
- Version constraints (e.g., "requires Metamod >= 2.0")
- Dependency conflict detection and resolution
- Cascade uninstall option
- Dependency graph visualization
- Optional vs required dependencies
