# Quick Start Guide - System Settings & Email Management

## Setup Instructions

### Step 1: Database Migration

The new features require database tables. When you start the application, the tables will be created automatically:

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000
```

The following tables will be created:
- `system_settings` - Stores global configuration
- `password_reset_tokens` - Stores password reset tokens

### Step 2: Access System Settings (Admin Only)

1. Log in with an admin account (default: `admin` / `admin123`)
2. Click on "System Settings" in the navigation bar
3. The system settings page will load with current configuration

### Step 3: Configure Email System

#### Option 1: Using Gmail SMTP

1. In System Settings, enable "Email System"
2. Select "SMTP" as the email provider
3. Configure the following:
   ```
   SMTP Host: smtp.gmail.com
   SMTP Port: 587
   SMTP Username: your-email@gmail.com
   SMTP Password: your-app-password  (NOT your regular password!)
   Enable TLS: Yes
   From Email: your-email@gmail.com
   From Name: CS2 Server Manager
   ```

4. **Generate Gmail App Password**:
   - Go to https://myaccount.google.com/apppasswords
   - Sign in with your Google account
   - Generate a new app password for "Mail"
   - Use this 16-character password in SMTP Password field

5. Click "Save Settings"

#### Option 2: Using Other SMTP Providers

For other SMTP providers (e.g., Outlook, SendGrid, Mailgun):

1. Get your SMTP credentials from your provider
2. In System Settings:
   ```
   SMTP Host: smtp.your-provider.com
   SMTP Port: 587 (or as specified by provider)
   SMTP Username: your-username
   SMTP Password: your-password
   Enable TLS: Yes (usually)
   From Email: noreply@yourdomain.com
   From Name: CS2 Server Manager
   ```

3. Click "Save Settings"

### Step 4: Configure Proxy Settings (Optional)

If your servers need to download from GitHub through a proxy:

1. In System Settings, select "Default Proxy Mode":
   - **Direct Connection**: Download directly (default)
   - **Use Panel Server Proxy**: Download via panel server
   - **Use GitHub Proxy URL**: Use custom proxy

2. If using GitHub Proxy URL, enter the URL (e.g., `https://ghfast.top`)

3. Click "Save Settings"

### Step 5: Test Password Reset

1. Log out from your account
2. On the login page, click "Forgot Password"
3. Enter your email address
4. Complete the CAPTCHA
5. Click "Send Reset Link"
6. Check your email inbox
7. Click the reset link in the email
8. Enter a new password
9. Log in with the new password

## Common Issues and Solutions

### Email Not Received

**Problem**: Password reset email is not arriving

**Solutions**:
1. Check spam/junk folder
2. Verify email settings in System Settings
3. Check application logs for errors:
   ```bash
   tail -f /var/log/cs2-manager.log
   # or check Docker logs
   docker logs -f cs2-manager
   ```
4. Ensure SMTP credentials are correct
5. For Gmail, ensure you're using App Password, not regular password

### Gmail SMTP Authentication Failed

**Problem**: "Authentication failed" error with Gmail

**Solutions**:
1. Use App Password instead of regular password
2. Enable "Less secure app access" (not recommended)
3. Check if 2-factor authentication is enabled (required for App Passwords)

### System Settings Not Accessible

**Problem**: Cannot access System Settings page

**Solutions**:
1. Verify you're logged in as admin user
2. Check if `is_admin=true` in database:
   ```sql
   SELECT id, username, is_admin FROM users WHERE username='admin';
   UPDATE users SET is_admin=true WHERE username='admin';
   ```
3. Clear browser cache and cookies
4. Check browser console for JavaScript errors

### Password Reset Link Expired

**Problem**: "Invalid or expired reset token" error

**Solutions**:
1. Request a new password reset link
2. Reset links expire after 1 hour
3. Links can only be used once

### SMTP Connection Timeout

**Problem**: Email sending times out

**Solutions**:
1. Check SMTP host and port
2. Verify firewall allows outgoing connections on SMTP port
3. For port 587, ensure TLS is enabled
4. Try port 465 with SSL instead of TLS
5. Check if your hosting provider blocks SMTP ports

## Security Best Practices

1. **Change Default Admin Password**:
   ```
   After first login, change the default admin password
   ```

2. **Use Strong SMTP Password**:
   - Use app-specific passwords
   - Never share SMTP credentials
   - Rotate passwords regularly

3. **Enable TLS for SMTP**:
   - Always use TLS/SSL for SMTP connections
   - Port 587 with TLS is recommended

4. **Monitor Password Reset Attempts**:
   - Check logs for suspicious activity
   - Implement rate limiting if needed

5. **Backup Database Regularly**:
   - System settings are stored in database
   - Regular backups prevent data loss

## Advanced Configuration

### Custom Email Templates

Email templates are located in `services/email_service.py`. To customize:

1. Edit the `get_password_reset_template()` method
2. Modify HTML and text content
3. Restart the application

### Database Query Examples

**View all system settings**:
```sql
SELECT * FROM system_settings;
```

**View password reset tokens**:
```sql
SELECT * FROM password_reset_tokens 
WHERE used=false AND expires_at > NOW();
```

**Clear expired tokens**:
```sql
DELETE FROM password_reset_tokens 
WHERE used=true OR expires_at < NOW();
```

### Proxy Mode Behavior

- **Direct**: Servers download directly from GitHub
- **Panel**: Downloads go through panel server first, then uploaded to game server
- **GitHub URL**: All GitHub URLs are rewritten to use proxy (e.g., github.com → ghfast.top)

## Troubleshooting Commands

**Check email configuration**:
```bash
python3 -c "
from services.email_service import email_service
import asyncio
from modules.database import async_session_maker
from modules import SystemSettings

async def check():
    async with async_session_maker() as db:
        settings = await SystemSettings.get_or_create_settings(db)
        print(f'Email enabled: {settings.email_enabled}')
        print(f'SMTP host: {settings.smtp_host}')
        print(f'SMTP port: {settings.smtp_port}')

asyncio.run(check())
"
```

**Test SMTP connection**:
```bash
python3 -c "
import smtplib

host = 'smtp.gmail.com'
port = 587
username = 'your-email@gmail.com'
password = 'your-app-password'

server = smtplib.SMTP(host, port)
server.starttls()
server.login(username, password)
print('SMTP connection successful!')
server.quit()
"
```

## Support

For issues or questions:
1. Check the documentation in `docs/SYSTEM_SETTINGS_EMAIL.md`
2. Review application logs
3. Create an issue on GitHub
4. Contact the maintainer

## Changelog

### v1.1.0 (Current)
- Added system settings page (admin only)
- Added email management system (SMTP)
- Added password reset flow
- Added i18n support for new features
- Added comprehensive documentation

### Future Enhancements
- Gmail API support (OAuth2)
- Email verification for new users
- Two-factor authentication
- Custom email templates UI
- Email notification settings
