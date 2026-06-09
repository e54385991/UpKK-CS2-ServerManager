# Testing the Plugin Management Feature

## Prerequisites

1. Database (MySQL) running
2. Redis running
3. Python dependencies installed (`pip install -r requirements.txt`)
4. CS2 server configured in the system

## Step 1: Start the Application

```bash
cd /home/runner/work/CS2-ServerManager/CS2-ServerManager
python main.py
```

The application should start on `http://localhost:8000` (or configured port).

## Step 2: Populate Sample Plugins

In a separate terminal:

```bash
cd /home/runner/work/CS2-ServerManager/CS2-ServerManager
python scripts/populate_plugins.py
```

Expected output:
```
Adding sample plugins to database...
✓ Successfully added 17 sample plugins to the database!
```

If you see "Database already contains X plugins. Skipping population.", the data is already loaded.

## Step 3: Access the Plugin Tab

1. Log in to the web interface
2. Navigate to a server detail page (`/servers-ui/{server_id}`)
3. Click on the "Plugins" tab (puzzle piece icon)

You should see:
- Left sidebar with categories (All Plugins, Utility, Gameplay, Admin, Chat, Statistics, Cosmetic, Other)
- Left sidebar bottom showing "Installed Plugins" (initially empty)
- Main area showing a grid of plugin cards
- Pagination controls at the bottom

## Step 4: Test Category Filtering

1. Click on different categories in the left sidebar
2. Observe that the plugin grid updates to show only plugins in that category
3. The "All Plugins" option shows all plugins
4. Category buttons highlight when active

## Step 5: Test Plugin Installation

1. Find a plugin that interests you (e.g., "Admin Management")
2. Click the "Install" button
3. A modal should appear with:
   - Plugin name and description
   - Optional "Custom Download URL" field
   - Optional "Configuration (JSON)" field (if plugin requires config)
   - Install button
4. Click "Install" in the modal
5. Wait for the installation to complete
6. Check the "Installed Plugins" sidebar - the plugin should appear there
7. The plugin card should now show "Installed" instead of the install button

## Step 6: Test Dependency Installation

1. Try installing "Teleport Manager" or "Team Balancer" (these have dependencies)
2. Observe that:
   - A warning appears: "This plugin has dependencies that will be installed automatically"
   - After installation, both the plugin and its dependencies appear in "Installed Plugins"

## Step 7: Test Plugin Uninstallation

1. In the "Installed Plugins" sidebar, click the trash icon next to an installed plugin
2. Confirm the uninstallation in the dialog
3. The plugin should be removed from the "Installed Plugins" list
4. The plugin card in the grid should show "Install" button again

## Step 8: Test Pagination

1. Make sure you're viewing "All Plugins"
2. There should be 17 plugins total, with 20 per page (default)
3. All plugins should appear on page 1
4. If you add more plugins, pagination controls will appear

## Step 9: Test Custom Download URL

1. Click "Install" on any plugin
2. In the modal, enter a custom URL in the "Custom Download URL" field
3. Click "Install"
4. The system will use your custom URL instead of the default

**Note**: For actual testing, you'll need a valid tar.gz URL that your server can download.

## Step 10: Test Configuration

1. Install a plugin that requires configuration (check `config_required` flag)
2. In the install modal, the "Configuration (JSON)" field should appear
3. Enter valid JSON configuration (e.g., `{"key": "value"}`)
4. Click "Install"
5. The configuration is saved and can be retrieved later

## Expected Behavior

### Plugin Grid
- 2 columns on desktop, 1 on mobile
- Each card shows:
  - Display name
  - Category badge (colored)
  - Description
  - Version and author
  - Dependency indicator (if applicable)
  - Install/Installed status
  - Homepage link (if available)

### Category Sidebar
- "All Plugins" with total count
- Each category with icon
- Active category highlighted in blue

### Installed Plugins Sidebar
- List of installed plugins
- Version numbers
- Uninstall button for each

### Installation Process
1. Modal appears
2. "Installing..." message shown
3. Success toast notification
4. Modal closes
5. Installed list updates
6. Plugin card updates to "Installed"

### Uninstallation Process
1. Confirmation dialog
2. Success toast notification
3. Plugin removed from installed list
4. Plugin card updates to "Install"

## Troubleshooting

### Plugins Not Loading
- Check browser console for errors
- Verify API endpoints are accessible (`/api/plugins`, `/api/plugins/categories`)
- Check server logs for errors

### Installation Fails
- Verify SSH connection to the CS2 server is working
- Check that the download URL is accessible from the server
- Verify the server has write permissions to the game directory
- Check server logs for detailed error messages

### Database Errors
- Ensure database tables are created (`plugins`, `installed_plugins`)
- Run migrations if needed
- Check database connection settings

### Permission Errors
- Verify you're logged in as a user who owns the server
- Check that the server exists and is accessible

## Sample Plugin URLs

For testing with real downloads, you can use these example URLs (replace with actual plugin URLs):

```
https://github.com/example/plugin/releases/download/v1.0.0/plugin.tar.gz
```

## What to Verify

- [ ] UI renders correctly
- [ ] Categories load and filter works
- [ ] Pagination displays (if >20 plugins)
- [ ] Plugin cards show all information
- [ ] Install modal opens and closes
- [ ] Custom URL field accepts input
- [ ] Configuration field accepts JSON
- [ ] Dependencies show warning
- [ ] Installation creates database record
- [ ] Installed plugins sidebar updates
- [ ] Uninstall confirmation appears
- [ ] Uninstall removes from list
- [ ] Toast notifications appear
- [ ] No console errors
- [ ] No security warnings
- [ ] External links open safely

## Performance Notes

- Plugin list is paginated for performance
- Category filtering is server-side (fast)
- Installed plugins list is cached per server
- Installation is async with progress feedback

## Security Notes

- All shell commands are properly escaped
- Download URLs are validated
- File paths are sanitized
- XSS prevention in place
- Access control enforced
- CodeQL verified (0 alerts)

## Next Steps After Testing

1. Report any bugs or issues found
2. Suggest UI improvements
3. Request additional features
4. Add real plugin data
5. Configure actual plugin repositories
6. Set up plugin update notifications
