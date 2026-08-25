# GitHub Personal Access Token Upgrade Notes

## Overview
This migration adds support for GitHub Fine-grained personal access tokens. Users can now configure their tokens in the profile center to access private repositories and get better API rate limits.

## Upgrade

The current application supports PostgreSQL 18+ only. The `github_token` column is part of the reviewed Alembic schema and is upgraded automatically at application startup; do not run feature-specific SQL files.

### 1. Update Application Code
```bash
# Pull latest code from the repository
git pull origin main

# Or if you're using a specific branch
git checkout copilot/add-fine-grained-tokens-support
git pull
```

### 2. Restart Application
```bash
# If using systemd
sudo systemctl start cs2-server-manager
sudo systemctl status cs2-server-manager

# If using 1Panel, restart from the control panel

# If using uv directly
uv run --no-dev --python 3.14 --locked uvicorn main:app --host 0.0.0.0 --port 8000
```

### 3. Verify Schema and Application
```bash
uv run python -m modules.db_admin check
```

Then review the application startup log and test the login page.

### 4. Test Token Configuration
1. Log in to the web interface
2. Navigate to Profile (Personal Center)
3. Scroll to "GitHub Personal Access Token" field
4. Verify the field is visible and functional

## Rollback

Production rollback uses a previously verified PostgreSQL backup together with the matching application version. Do not manually remove schema objects or automatically run a data-losing downgrade. See [PostgreSQL 18+ migration and operations](POSTGRESQL_MIGRATION.md).

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

### Issue: Schema check fails

**Solution**:
1. Confirm PostgreSQL is version 18 or newer
2. Check database connection credentials
3. Review the Alembic startup log and `uv run python -m modules.db_admin status`

### Issue: Application won't start after migration
**Solution**:
1. Check application logs for errors
2. Verify Python dependencies are installed
3. Ensure database connection is working
4. Restore the verified PostgreSQL backup and matching application version if rollback is required

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
3. Create an issue on GitHub: https://github.com/e54385991/UpKK-CS2-ServerManager/issues

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
