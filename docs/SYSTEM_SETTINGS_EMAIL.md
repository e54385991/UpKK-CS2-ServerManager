# System Settings and Email Management Features

## Overview

This document describes the new system settings and email management features added to CS2 Server Manager.

## Features

### 1. System Settings Page (Admin Only)

A comprehensive settings page accessible only by administrators to configure global system behavior.

#### Features:
- **Download Proxy Settings**: Configure default proxy mode for GitHub downloads
  - Direct Connection: Download directly from GitHub (default)
  - Use Panel Server Proxy: Download via panel server (recommended for China mainland)
  - Use GitHub Proxy URL: Use custom GitHub proxy URL
- **Email Settings**: Configure email system for password reset and notifications
  - SMTP configuration support
  - Gmail API support (coming soon)
  - Enable/disable email system
  - Configure sender information

#### Access:
- Only users with `is_admin=true` can access this page
- URL: `/system-settings`
- API endpoint: `/api/system/settings`

### 2. Email Management System

Email system for sending password reset emails and future notifications.

#### Features:
- SMTP email provider support
- Configurable sender email and name
- HTML and plain text email templates
- Secure password reset flow

#### SMTP Configuration:
Configure in System Settings:
- SMTP Host (e.g., smtp.gmail.com)
- SMTP Port (default: 587)
- SMTP Username
- SMTP Password
- TLS enabled/disabled

### 3. Password Reset Flow

Users can reset their password via email if they forget it.

#### Workflow:
1. User clicks "Forgot Password" on login page
2. User enters email address and completes CAPTCHA
3. System sends password reset email (if email exists)
4. User clicks link in email (valid for 1 hour)
5. User enters new password
6. User can log in with new password

#### Features:
- Secure token-based reset (tokens expire after 1 hour)
- Email enumeration protection (always returns success)
- CAPTCHA protection against abuse
- Tokens can only be used once
- Beautiful HTML email template

### 4. Internationalization (i18n)

All new features fully support multi-language:
- English (en-US)
- Chinese Simplified (zh-CN)

## API Endpoints

### System Settings

#### GET /api/system/settings
Get current system settings (admin only)

**Authentication**: Required (admin)

**Response**:
```json
{
  "id": 1,
  "default_proxy_mode": "direct",
  "github_proxy_url": null,
  "email_enabled": true,
  "email_provider": "smtp",
  "email_from_address": "noreply@example.com",
  "email_from_name": "CS2 Server Manager",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_username": "user@example.com",
  "smtp_use_tls": true,
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:00:00"
}
```

#### PUT /api/system/settings
Update system settings (admin only)

**Authentication**: Required (admin)

**Request Body**:
```json
{
  "default_proxy_mode": "panel",
  "email_enabled": true,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_username": "user@example.com",
  "smtp_password": "password",
  "smtp_use_tls": true
}
```

### Password Reset

#### POST /api/auth/forgot-password
Request password reset email

**Authentication**: Not required

**Request Body**:
```json
{
  "email": "user@example.com",
  "captcha_token": "abc123",
  "captcha_code": "1234"
}
```

**Response**:
```json
{
  "success": true,
  "message": "If an account with this email exists, a password reset link has been sent."
}
```

#### POST /api/auth/reset-password-with-token
Reset password using token

**Authentication**: Not required

**Request Body**:
```json
{
  "token": "reset_token_here",
  "new_password": "newpassword123"
}
```

**Response**:
```json
{
  "success": true,
  "message": "Password reset successfully. You can now log in with your new password."
}
```

## Database Models

### SystemSettings
- `id`: Primary key
- `default_proxy_mode`: Default proxy mode (direct, panel, github_url)
- `github_proxy_url`: GitHub proxy URL
- `email_enabled`: Enable/disable email system
- `email_provider`: Email provider (smtp, gmail)
- `email_from_address`: Sender email address
- `email_from_name`: Sender name
- `gmail_credentials_json`: Gmail API credentials (future)
- `gmail_token_json`: Gmail API token (future)
- `smtp_host`: SMTP server host
- `smtp_port`: SMTP server port
- `smtp_username`: SMTP username
- `smtp_password`: SMTP password (encrypted)
- `smtp_use_tls`: Use TLS for SMTP
- `created_at`: Creation timestamp
- `updated_at`: Update timestamp

### PasswordResetToken
- `id`: Primary key
- `user_id`: Foreign key to users table
- `token`: Unique reset token
- `expires_at`: Token expiration time
- `used`: Whether token has been used
- `created_at`: Creation timestamp

## Security Considerations

1. **Admin Access**: System settings are protected by admin-only authentication
2. **Email Enumeration**: Forgot password always returns success to prevent email enumeration
3. **Token Security**: Reset tokens are:
   - Cryptographically secure (64 characters)
   - Single-use only
   - Time-limited (1 hour)
4. **CAPTCHA Protection**: All password reset requests require CAPTCHA
5. **SMTP Credentials**: Stored securely in database

## Configuration

### Enabling Email System

1. Log in as admin
2. Navigate to System Settings
3. Enable "Email System"
4. Configure SMTP settings:
   - SMTP Host
   - SMTP Port (587 for TLS, 465 for SSL)
   - SMTP Username
   - SMTP Password
   - Enable TLS (recommended)
5. Set sender email and name
6. Save settings

### Testing Email Configuration

After configuring email, test by:
1. Log out
2. Click "Forgot Password"
3. Enter a valid email
4. Complete CAPTCHA
5. Check email inbox for password reset link

## Troubleshooting

### Email Not Sending

1. Check email settings in System Settings
2. Verify SMTP credentials are correct
3. Check SMTP host and port
4. Ensure TLS is enabled for port 587
5. Check application logs for errors

### Gmail SMTP

For Gmail SMTP:
- Host: smtp.gmail.com
- Port: 587
- TLS: Enabled
- **Important**: Use App Password, not regular password
- Generate App Password at: https://myaccount.google.com/apppasswords

### Password Reset Link Not Working

1. Check if email system is enabled
2. Verify token hasn't expired (1 hour limit)
3. Ensure token hasn't been used already
4. Check database for token record

## Future Enhancements

- Gmail API support (OAuth2)
- Email templates for other notifications
- Email verification for new registrations
- Two-factor authentication via email
- Customizable email templates
