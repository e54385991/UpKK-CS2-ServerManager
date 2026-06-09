# Migration Instructions: GitHub Personal Access Token Support

## Overview
This migration adds support for GitHub Fine-grained personal access tokens. Users can now configure their tokens in the profile center to access private repositories and get better API rate limits.

## Prerequisites
- Database access with ALTER TABLE permissions
- Backup of current database (recommended)
- Application downtime during migration (< 1 minute)

## Step-by-Step Migration

### 1. Backup Database (Recommended)
```bash
# Create a backup before migration
mysqldump -u your_user -p cs2_manager > backup_$(date +%Y%m%d_%H%M%S).sql
```

### 2. Stop Application
```bash
# If using systemd
sudo systemctl stop cs2-server-manager

# If using 1Panel or direct uvicorn, stop the process
```

### 3. Run Migration
```bash
# Navigate to the repository directory
cd /path/to/CS2-ServerManager

# Run the migration script
mysql -u your_user -p cs2_manager < db/migrations/add_github_token.sql

# Or manually execute the SQL:
# ALTER TABLE `users` 
# ADD COLUMN `github_token` VARCHAR(255) NULL DEFAULT NULL 
# COMMENT 'GitHub Fine-grained personal access token for API authentication' 
# AFTER `steam_api_key`;
```

### 4. Verify Migration
```bash
# Connect to database
mysql -u your_user -p cs2_manager

# Verify column was added
DESC users;

# Should show github_token column with:
# - Type: varchar(255)
# - Null: YES
# - Default: NULL

# Exit MySQL
exit
```

### 5. Update Application Code
```bash
# Pull latest code from the repository
git pull origin main

# Or if you're using a specific branch
git checkout copilot/add-fine-grained-tokens-support
git pull
```

### 6. Restart Application
```bash
# If using systemd
sudo systemctl start cs2-server-manager
sudo systemctl status cs2-server-manager

# If using 1Panel, restart from the control panel

# If using direct uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 7. Verify Application
```bash
# Check application logs for any errors
tail -f /path/to/logs/app.log

# Test login to the web interface
# Navigate to: http://your-server:8000/login
```

### 8. Test Token Configuration
1. Log in to the web interface
2. Navigate to Profile (Personal Center)
3. Scroll to "GitHub Personal Access Token" field
4. Verify the field is visible and functional

## Rollback Instructions

If you need to rollback the migration:

```bash
# Stop the application
sudo systemctl stop cs2-server-manager

# Restore from backup
mysql -u your_user -p cs2_manager < backup_YYYYMMDD_HHMMSS.sql

# Or manually remove the column
mysql -u your_user -p cs2_manager -e "ALTER TABLE users DROP COLUMN github_token;"

# Revert application code
git checkout main  # or your previous version
git pull

# Restart application
sudo systemctl start cs2-server-manager
```

## Post-Migration User Guide

After successful migration, inform users:

### For End Users
1. Navigate to Profile page
2. Get a GitHub Personal Access Token from: https://github.com/settings/tokens?type=beta
3. Configure token with these settings:
   - **Token name**: CS2 Server Manager
   - **Expiration**: 90 days (recommended)
   - **Repository access**: Select repositories you want to access
   - **Permissions**: Contents (Read-only)
4. Copy the generated token
5. Paste it in the "GitHub Personal Access Token" field
6. Complete CAPTCHA and save

### Benefits for Users
- Access private GitHub repositories for plugin installation
- Better API rate limits (5000/hour vs 60/hour)
- More reliable GitHub operations

## Troubleshooting

### Issue: Column already exists
```
ERROR 1060 (42S21): Duplicate column name 'github_token'
```
**Solution**: Column already exists, migration already applied. Skip to step 5.

### Issue: Migration script fails
**Solution**: 
1. Check database connection credentials
2. Verify user has ALTER TABLE permissions
3. Check database is accessible

### Issue: Application won't start after migration
**Solution**:
1. Check application logs for errors
2. Verify Python dependencies are installed
3. Ensure database connection is working
4. Rollback and retry migration

### Issue: Token field not showing in UI
**Solution**:
1. Clear browser cache
2. Hard refresh (Ctrl+F5)
3. Check browser console for JavaScript errors
4. Verify template files were updated

## Support

For issues or questions:
1. Check logs: `/var/log/cs2-server-manager/`
2. Review documentation: `docs/GITHUB_TOKEN.md`
3. Create an issue on GitHub: https://github.com/e54385991/CS2-ServerManager/issues

## Validation Checklist

After migration, verify:
- [ ] Database column added successfully
- [ ] Application starts without errors
- [ ] Web interface loads correctly
- [ ] Login functionality works
- [ ] Profile page displays token field
- [ ] Token can be saved and retrieved
- [ ] GitHub API requests work as before
- [ ] Existing functionality not affected

## Timeline

Expected migration time: **< 5 minutes**
- Backup: 1 minute
- Stop app: 10 seconds  
- Migration: 1 second
- Code update: 30 seconds
- Restart: 30 seconds
- Verification: 2 minutes

## Notes

- Migration is backwards compatible (column is nullable)
- Existing users will have NULL for github_token initially
- No data loss or changes to existing functionality
- Feature is opt-in (users can choose to configure token)
