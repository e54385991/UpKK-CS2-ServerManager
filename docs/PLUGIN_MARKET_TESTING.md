# Plugin Market Feature - Testing and Deployment Guide

## Testing Instructions

### 1. Database Migration Testing
The plugin_market_items table will be created automatically when the application starts. To verify:

1. Start the application:
   ```bash
   python3 main.py
   ```

2. Check the startup logs for:
   ```
   Database initialized successfully!
   ```

3. Verify the table exists:
   ```sql
   SHOW TABLES LIKE 'plugin_market_items';
   DESCRIBE plugin_market_items;
   ```

### 2. Plugin Data Initialization

Run the initialization script to populate the marketplace:

```bash
cd /home/runner/work/CS2-ServerManager/CS2-ServerManager
python3 scripts/init_plugin_market.py
```

Expected output:
```
Adding 10 plugins to the market...
✓ Successfully added 10 plugins to the market!

Plugins by category:
  依赖: 4 plugins
    - Metamod:Source
    - CounterStrikeSharp
    - Client CVar Value
    - SQL MM - MySQL/MariaDB Support
  功能: 5 plugins
    - CS2KZ - Metamod Plugin
    - Multi Addon Manager
    - CS2Fixes
    - MatchZy
    - Simple Admin
  娱乐: 1 plugins
    - OpenMod
```

### 3. UI Testing

#### Browse Plugins
1. Navigate to http://localhost:8000/plugin-market (after login)
2. Verify you see a grid of plugin cards
3. Check that each card displays:
   - Plugin name
   - Short description
   - Category badge
   - Install count
   - Author (if available)
   - Tags

#### Search Functionality
1. Type "kz" in the search box
2. Verify CS2KZ plugin appears
3. Type "metamod" in the search box
4. Verify Metamod:Source and CS2KZ appear
5. Clear search and verify all plugins return

#### Filter by Category
1. Select "功能" (Functionality) from category dropdown
2. Verify only functionality plugins appear
3. Select "依赖" (Dependency) from category dropdown
4. Verify only dependency plugins appear
5. Select "All Categories"
6. Verify all plugins appear

#### Pagination
1. If you have more than 20 plugins, verify pagination controls appear
2. Click "Next" and verify page changes
3. Click "Previous" and verify page changes
4. Click specific page numbers and verify correct plugins load

#### Plugin Details Modal
1. Click on any plugin card
2. Verify modal opens with:
   - Full description
   - Tags
   - GitHub repository link
   - Related URLs (dependencies) if applicable
   - Server dropdown
   - Install button

### 4. Installation Testing

#### Prerequisites
- At least one server configured in the system
- Server must have SSH access
- CS2 must be deployed on the server

#### Test Single Plugin Installation
1. Open plugin details for "Simple Admin"
2. Select a server from dropdown
3. Click "Install Plugin"
4. Verify installation progress messages appear
5. Verify success message with details
6. Check server's addons folder for installed files

#### Test Multi-URL Installation
1. Open plugin details for "CounterStrikeSharp" (has Metamod as dependency)
2. Select a server from dropdown
3. Click "Install Plugin"
4. Verify messages for:
   - "Processing Main plugin: https://github.com/roflmuffin/CounterStrikeSharp"
   - "Processing Dependency 0: https://github.com/alliedmodders/metamod-source"
5. Verify both installations complete
6. Check server's addons folder for both plugins

#### Test Installation Error Handling
1. Try installing without selecting a server
2. Verify install button is disabled
3. Select a server with no CS2 installed
4. Verify appropriate error message appears

### 5. API Testing

Use curl or Postman to test API endpoints:

#### List Plugins
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8000/api/plugin-market/items?page=1&page_size=10"
```

Expected: JSON with items, total, page info, and categories

#### Get Categories
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8000/api/plugin-market/categories"
```

Expected: JSON with list of categories

#### Search Plugins
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8000/api/plugin-market/items?search=metamod"
```

Expected: JSON with filtered results

#### Filter by Category
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8000/api/plugin-market/items?category=依赖"
```

Expected: JSON with dependency plugins only

#### Install Plugin
```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": 1, "exclude_dirs": []}' \
  "http://localhost:8000/api/plugin-market/servers/1/install/1"
```

Expected: JSON with success status and installation details

## Deployment Checklist

### Pre-Deployment
- [ ] Backup database
- [ ] Review all code changes
- [ ] Run security scanner (CodeQL)
- [ ] Test on staging environment
- [ ] Verify all dependencies installed

### Deployment Steps

1. **Pull Latest Code**
   ```bash
   cd /home/runner/work/CS2-ServerManager/CS2-ServerManager
   git pull origin main
   ```

2. **Install Dependencies** (if any new ones)
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Stop Application**
   ```bash
   # If using systemd
   sudo systemctl stop cs2-manager
   
   # Or if running manually
   pkill -f "python3 main.py"
   ```

4. **Database Migration**
   ```bash
   # The migration happens automatically on startup
   # No manual SQL needed
   ```

5. **Initialize Plugin Data**
   ```bash
   python3 scripts/init_plugin_market.py
   ```

6. **Start Application**
   ```bash
   # If using systemd
   sudo systemctl start cs2-manager
   
   # Or manually
   python3 main.py
   ```

7. **Verify Deployment**
   - Check application logs for errors
   - Access /plugin-market URL
   - Verify plugins load correctly
   - Test one installation

### Post-Deployment
- [ ] Monitor logs for errors
- [ ] Verify all features working
- [ ] Check database performance
- [ ] Monitor server resource usage
- [ ] Collect user feedback

## Rollback Plan

If issues occur:

1. **Stop Application**
2. **Revert Code**
   ```bash
   git revert <commit-hash>
   ```
3. **Optionally Drop Table** (if needed)
   ```sql
   DROP TABLE IF EXISTS plugin_market_items;
   ```
4. **Restart Application**

## Performance Considerations

### Database Indexes
The plugin_market_items table has indexes on:
- `name` - for search performance
- `category` - for filtering performance

### Query Optimization
- Pagination limits results to 20 per page by default
- Search uses SQL LIKE with leading wildcard (may be slow with large datasets)
- Consider full-text search for production with >1000 plugins

### Caching Recommendations
Consider adding Redis caching for:
- Plugin list (cache for 5 minutes)
- Category list (cache for 1 hour)
- Plugin details (cache for 10 minutes)

## Monitoring

Key metrics to monitor:
- Plugin installation success rate
- Average installation time
- Most popular plugins (by install_count)
- Search query performance
- API response times

## Troubleshooting

### Plugins Not Showing
**Symptom**: Empty plugin grid
**Solutions**:
1. Check if init script ran: `SELECT COUNT(*) FROM plugin_market_items;`
2. Run init script: `python3 scripts/init_plugin_market.py`
3. Check logs for database connection errors

### Installation Fails
**Symptom**: Plugin installation returns error
**Solutions**:
1. Verify server SSH connection
2. Check server has CS2 deployed
3. Verify GitHub URL is accessible
4. Check server disk space
5. Review installation logs in UI

### Search Not Working
**Symptom**: Search returns no results
**Solutions**:
1. Verify search terms are correct
2. Try filtering by category instead
3. Check database connection
4. Verify is_active=1 for plugins

### Slow Performance
**Symptom**: Plugin list loads slowly
**Solutions**:
1. Add database indexes if missing
2. Reduce page_size parameter
3. Implement Redis caching
4. Optimize search queries

## Future Enhancements

Based on usage patterns, consider:
1. **Admin UI** - Add/edit plugins via web interface
2. **Plugin Ratings** - User ratings and reviews
3. **Auto-Updates** - Automatic plugin updates
4. **Compatibility** - Version compatibility checking
5. **Screenshots** - Add plugin screenshots
6. **Installation History** - Track installation history per server
7. **Bundles** - Pre-configured plugin bundles for common setups

## Support

For issues or questions:
1. Check this testing guide
2. Review docs/PLUGIN_MARKET.md
3. Check application logs
4. Create GitHub issue with:
   - Error message
   - Steps to reproduce
   - Server environment details
   - Browser console errors (if UI issue)
