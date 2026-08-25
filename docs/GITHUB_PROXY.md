# Download Proxy Configuration Feature

## Overview

This feature provides two mutually exclusive proxy modes for CS2 Server Manager to improve download speeds in restricted networks:

1. **Panel Server Proxy** - Downloads via the web panel server first (covers SteamCMD + GitHub plugins)
2. **GitHub URL Proxy** - Direct download with proxy URL (covers GitHub plugins only)

## Proxy Modes Comparison

| Feature | Panel Server Proxy | GitHub URL Proxy | Direct Connection |
|---------|-------------------|------------------|-------------------|
| SteamCMD Support | ✅ Yes | ❌ No | ✅ Yes |
| GitHub Plugins | ✅ Yes | ✅ Yes | ✅ Yes |
| Progress Tracking | ✅ Detailed (Download + Upload) | ⚠️ Limited (curl) | ⚠️ Limited |
| Requirements | Panel has good foreign access | Proxy service available | Good network both sides |
| Network Flow | GitHub → Panel → Game Server | GitHub → Proxy → Game Server | GitHub → Game Server |
| Best For | Panel overseas, server in China | All servers in China | Both have good access |
| Third-party Dependency | ❌ None | ✅ Proxy service | ❌ None |

## Implementation Details

### Database Changes

1. **New Columns** added to `servers` table:
   - `github_proxy` (varchar(500)) - GitHub proxy URL
   - `use_panel_proxy` (boolean) - Enable panel server proxy mode
   - **Mutually exclusive**: Only one can be set at a time

2. **Schema Migration**:
   - The fields are part of the versioned Alembic schema and upgrade automatically at startup.

### Backend Changes

1. **Models** (`modules/models.py`)
   - Added `github_proxy` field to `Server` model
   - Added `use_panel_proxy` field to `Server` model

2. **Schemas** (`modules/schemas.py`)
   - Added both fields to `ServerCreate`, `ServerUpdate`, `ServerResponse`
   - Added `model_validator` to enforce mutual exclusivity
   - Validation error if both are set simultaneously

3. **HTTP Helper** (`modules/http_helper.py`)
   - Added `download_file()` method for streaming downloads with progress
   - Progress callback: `(bytes_downloaded, total_bytes)`
   - Used by panel proxy mode for all downloads
   - GitHub URL proxy: Prepends proxy base to GitHub URLs

4. **SSH Manager** (`services/ssh_manager.py`)
   - Added `upload_file_with_progress()` method for SFTP uploads with progress
   - Progress callback: `(bytes_uploaded, total_bytes)`
   - `deploy_cs2_server()`: 
     - Checks `server.use_panel_proxy` for SteamCMD download
     - If enabled: Downloads to panel → Uploads via SFTP → Extracts on server
     - If disabled: Direct wget download on server
   - `_fetch_github_release_url`: Uses `github_proxy` if set

5. **GitHub Plugin Routes** (`api/routes/github_plugins.py`)
   - `install_github_plugin()`: 
     - Checks `server.use_panel_proxy` (not request parameter)
     - Panel mode: Download → Upload → Extract
     - GitHub proxy mode: Uses `server.github_proxy` for curl
     - Direct mode: No proxy

### Frontend Changes

1. **Configuration Tab** (`templates/server_detail_includes/configuration_tab.html`)
   - Renamed to "Download Proxy Configuration"
   - **View Mode**:
     - Shows current mode badge (Panel Proxy / GitHub URL Proxy / Direct)
     - Displays active proxy details
     - Info alert explaining the choice
   - **Edit Mode**:
     - Toggle switch for "Use Panel Server Proxy"
     - Text input for "GitHub Proxy URL"
     - Automatic mutual exclusivity enforcement
     - Disables GitHub URL input when panel proxy enabled
     - Clears opposite mode when one is selected
   - Buttons: Save Proxy Configuration, Cancel, Clear All

2. **Actions Tab** (`templates/server_detail_includes/actions_tab.html`)
   - Removed per-request panel proxy toggle
   - Shows read-only download mode status in Installation Options
   - Badge shows current server configuration
   - Links to Configuration tab to change mode

3. **JavaScript** (`templates/server_detail_includes/scripts.html`)
   - Data variables:
     - `githubProxyForm`: Now includes both `github_proxy` and `use_panel_proxy`
   - Functions:
     - `loadGithubProxyForm()`: Loads both settings from server
     - `saveGithubProxy()`: Saves both settings
     - `clearGithubProxy()`: Clears both settings
   - Removed: `usePanelProxy` variable (now in server config)

3. **Translations**
   - English and Chinese translations for all UI elements
   - Keys include: `downloadProxyConfig`, `proxyMode`, `panelProxyMode`, `githubProxyMode`, etc.
   - See `static/locales/en-US.json` and `static/locales/zh-CN.json`

## Usage

### For End Users

#### Choosing a Proxy Mode

**Use Panel Server Proxy if:**
- ✅ Your web panel server has good access to GitHub/Steam (e.g., hosted overseas)
- ✅ Your game server has restricted access (e.g., in China)
- ✅ You want detailed progress tracking for all downloads
- ✅ You want coverage for both SteamCMD and GitHub plugins

**Use GitHub URL Proxy if:**
- ✅ You have a reliable proxy service (ghfast.top, ghproxy.com, etc.)
- ✅ Your game server can access the proxy
- ✅ You only need GitHub plugin support (not SteamCMD)
- ✅ You want minimal panel server resource usage

**Use Direct Connection if:**
- ✅ Both servers have good international network access
- ✅ No restrictions on GitHub/Steam access
- ✅ Fastest option when network is not restricted

#### Configuration Steps

1. **Navigate to Server Configuration**
   - Go to Server Detail page
   - Click on "Configuration" tab
   - Scroll to "Download Proxy Configuration" section

2. **Option A: Configure Panel Server Proxy**
   - Click "Edit" button
   - Enable "Use Panel Server Proxy" toggle
   - Click "Save Proxy Configuration"
   - **Note**: GitHub URL Proxy input will be disabled automatically

3. **Option B: Configure GitHub URL Proxy**
   - Click "Edit" button
   - Enter proxy base URL in "GitHub Proxy URL" (e.g., `https://ghfast.top`)
   - **Important**: Enter ONLY the base URL, do NOT include `/https://github.com`
   - Click "Save Proxy Configuration"
   - **Note**: Panel Server Proxy will be disabled automatically

4. **Clear All Proxy Settings**
   - Click "Edit" button
   - Click "Clear All" button
   - Click "Save Proxy Configuration"
   - Returns to direct connection mode

5. **Recommended Proxy Services** (for GitHub URL Proxy)
   - https://ghfast.top
   - https://ghproxy.com
   - https://mirror.ghproxy.com

### How It Works

#### Panel Server Proxy Mode

When `use_panel_proxy = true`:

1. **SteamCMD Download** (during server deployment):
   ```
   steamcdn-a.akamaihd.net → Panel Server (download)
                          → Panel Server → Game Server (SFTP upload)
                          → Game Server (extract)
   ```
   - Progress: "Download progress: 50% (5.2/10.4 MB)"
   - Progress: "Upload progress: 50% (5.2/10.4 MB)"

2. **GitHub Plugin Download**:
   ```
   github.com → Panel Server (download)
             → Panel Server → Game Server (SFTP upload)
             → Game Server (extract & install)
   ```
   - Same detailed progress tracking

3. **Advantages**:
   - ✅ Works for all downloads (SteamCMD + GitHub)
   - ✅ No third-party dependency
   - ✅ Detailed progress for download AND upload
   - ✅ Works even if game server is offline during download

4. **Requirements**:
   - Panel server needs good access to GitHub/Steam
   - Good SFTP connection between panel and game server

#### GitHub URL Proxy Mode

When `github_proxy` is set (e.g., "https://ghfast.top"):

1. **GitHub Plugin Download**:
   ```
   Original URL: https://github.com/owner/repo/releases/download/v1.0/file.zip
   Proxied URL:  https://ghfast.top/https://github.com/owner/repo/releases/download/v1.0/file.zip
   ```
   - Game server downloads directly via proxy
   - Limited progress (curl output)

2. **Coverage**:
   - ✅ GitHub plugins only
   - ❌ SteamCMD still uses direct download

3. **Behavior**:
   - GitHub API requests: Direct connection (proxy services don't support API)
   - File downloads: Proxied connection

#### Direct Connection Mode

When both are disabled:
- All downloads go directly from source to game server
- No proxy, no panel intermediary
- Fastest when network is good

1. **GitHub API Calls** (Direct - No Proxy)
   - User configures: `https://ghfast.top`
   - API request: `https://api.github.com/repos/owner/repo/releases/latest`
   - System uses: `https://api.github.com/repos/owner/repo/releases/latest` (Direct, no proxy)
   - Reason: Proxy services don't support API endpoints

2. **GitHub Release Downloads** (Proxied)
   - User configures: `https://ghfast.top`
   - Original URL: `https://github.com/owner/repo/releases/download/v1.0/file.zip`
   - System creates: `https://ghfast.top/https://github.com/owner/repo/releases/download/v1.0/file.zip`
   - Reason: This is what proxy services are designed for

3. **Affected Operations**
   - ✅ GitHub plugin file downloads (via UI) - Uses proxy
   - ✅ Plugin archive downloads - Uses proxy
   - ✅ CounterStrikeSharp file downloads - Uses proxy
   - ✅ CS2Fixes file downloads - Uses proxy
   - ⚠️ GitHub API requests - Direct connection (proxy not supported by services)

## Security Considerations

1. **Input Validation**
   - Proxy URL is optional (can be NULL/empty)
   - No special validation required as URL is used in curl commands
   - Users should only use trusted proxy services

2. **Trust Model**
   - Proxy services act as man-in-the-middle
   - Only use trusted proxy providers
   - Proxy services can see all GitHub requests

## Testing

Run the verification script:

```bash
cd /home/runner/work/UpKK-CS2-ServerManager/UpKK-CS2-ServerManager
python3 tests/verify_github_proxy_implementation.py
```

## Migration Steps

For existing deployments:

1. **Upgrade and Verify the Alembic Schema**
   ```bash
   uv run python -m modules.db_admin upgrade
   uv run python -m modules.db_admin check
   ```

2. **Restart Application**
   ```bash
   # Using docker-compose
   docker-compose restart

   # Or using systemd
   systemctl restart cs2-server-manager
   ```

3. **Verify**
   - Log in to the application
   - Navigate to any server's configuration tab
   - Verify "Download Proxy Configuration" section appears
   - Test toggling between modes
   - Verify mutual exclusivity works (can't enable both)
   - Check that badge shows correct mode

5. **Existing Data**
   - Servers with `github_proxy` set: Will continue to work (GitHub URL Proxy mode)
   - Servers without proxy: Will use Direct Connection mode
   - `use_panel_proxy` defaults to `false` for all existing servers

## Future Enhancements

Potential improvements:

1. **Global Proxy Setting**
   - Add user-level or system-level default proxy
   - Server-level proxy overrides global proxy

2. **Proxy Validation**
   - Test proxy connectivity before saving
   - Show proxy status (working/not working)

3. **Multiple Proxy Support**
   - Configure fallback proxies
   - Automatic failover to direct connection

4. **Proxy Statistics**
   - Track download speeds
   - Show proxy performance metrics

## Panel Server Proxy Mode (NEW)

### Overview

In addition to configuring a GitHub proxy on the game server, you can now use the **Panel Server Proxy Mode** which downloads files to the web panel server first, then uploads them to the game server via SFTP.

### When to Use Panel Server Proxy

This mode is recommended when:
- ✅ Your web panel server can access GitHub smoothly (e.g., hosted internationally)
- ✅ Your game server has restricted access to GitHub (e.g., in China)
- ✅ You want to see detailed download and upload progress
- ✅ You want to bypass the need for GitHub proxy services altogether

### How It Works

1. **Download Phase**: File is downloaded from GitHub to the web panel server (running this application)
   - Files are stored in a temporary directory isolated by user ID
   - Progress is tracked and displayed (e.g., "Download progress: 50% (5.2/10.4 MB)")

2. **Upload Phase**: File is uploaded from panel to game server via SFTP
   - Chunked upload with progress tracking
   - Progress is displayed (e.g., "Upload progress: 50% (5.2/10.4 MB)")

3. **Installation Phase**: File is extracted and installed on the game server
   - Same as normal installation flow

### Important Notes

- ⚠️ **GitHub Proxy Setting Ignored**: When panel proxy mode is enabled, the server's `github_proxy` setting is ignored
- 💾 **Temporary Storage**: Files are stored in `/tmp/cs2_panel_proxy_{user_id}/` on the panel server
- 🧹 **Auto Cleanup**: Temporary files are automatically deleted after successful upload or on error
- 🔒 **Security**: Each user has an isolated temporary directory (UID-based isolation)

### Usage Instructions

1. **Navigate to Actions Tab**
   - Go to Server Detail page
   - Click on "Actions" tab
   - Scroll to "GitHub Plugin Install" section

2. **Select Release and Asset**
   - Enter GitHub repository URL
   - Click "Fetch Releases"
   - Select a release and asset

3. **Enable Panel Proxy Mode**
   - In the "Installation Options" section
   - Toggle the switch "Use Panel Server Proxy"
   - Read the tooltip for more information

4. **Install Plugin**
   - Click "Install Plugin"
   - Monitor the progress in the operation modal:
     - "Downloading to panel server..." (with progress %)
     - "Uploading to server via SFTP..." (with progress %)
     - "Extracting archive..."
     - "Installing plugin files..."

### Backend Implementation

**New Schema Field** (`modules/schemas.py`):
```python
class GitHubPluginInstallRequest(SQLModel):
    download_url: str
    exclude_dirs: List[str] = []
    use_panel_proxy: bool = False  # NEW
```

**Download Function** (`modules/http_helper.py`):
```python
async def download_file(url, local_path, progress_callback=None)
```
- Streams file in chunks
- Calls progress callback with (bytes_downloaded, total_bytes)

**Upload Function** (`services/ssh_manager.py`):
```python
async def upload_file_with_progress(local_path, remote_path, server, progress_callback=None)
```
- Uploads file in 32KB chunks via SFTP
- Calls progress callback with (bytes_uploaded, total_bytes)

**Installation Flow** (`api/routes/github_plugins.py`):
1. Check if `use_panel_proxy` is enabled
2. If enabled:
   - Download to `/tmp/cs2_panel_proxy_{user_id}/{unique_id}/`
   - Upload to an operation-scoped directory such as
     `/tmp/upkk-plugin-{server_id}-{operation_id}/` on the remote server
   - Continue with extraction and installation
3. If disabled:
   - Use original flow (direct download on game server or via GitHub proxy)

### Frontend Changes

**UI Toggle** (`templates/server_detail_includes/actions_tab.html`):
- Added checkbox switch "Use Panel Server Proxy"
- Tooltip explaining when to use this mode
- Help text clarifying that github_proxy is ignored

**JavaScript** (`templates/server_detail_includes/scripts.html`):
- Added `usePanelProxy: false` to data
- Sends `use_panel_proxy` in installation request

**Translations** (English and Chinese):
- `usePanelProxy`: "Use Panel Server Proxy"
- `usePanelProxyTooltip`: Detailed explanation
- `usePanelProxyHint`: Usage hint

### Advantages Over GitHub Proxy

| Feature | GitHub Proxy | Panel Server Proxy |
|---------|--------------|-------------------|
| Requires proxy service | Yes | No |
| Detailed progress tracking | Limited (curl) | Yes (download + upload) |
| Works when game server offline | No | Yes (downloads first) |
| File size verification | Basic | Detailed |
| Network requirements | Game server → proxy → GitHub | Panel → GitHub, Panel → Game Server |
| Best for | Game server in restricted region | Panel in unrestricted region |

### Performance Considerations

**Advantages:**
- ✅ Better progress visibility
- ✅ Can pause/resume installations
- ✅ No dependency on third-party proxy services
- ✅ Works with any GitHub repository

**Trade-offs:**
- ⚠️ Requires good network between panel and game server
- ⚠️ Uses panel server disk space temporarily
- ⚠️ Slightly slower than direct download (extra hop)

### Troubleshooting

#### Panel Server Proxy Mode Issues

1. **Download Fails**
   - Check if panel server can access GitHub
   - Verify firewall allows outbound HTTPS connections
   - Check panel server disk space (`df -h /tmp`)
   - Review logs for HTTP errors

2. **Upload Fails**
   - Verify SFTP connection works (SSH credentials correct)
   - Check network connectivity between panel and game server
   - Ensure game server has disk space
   - Check file permissions on `/tmp` directory

3. **Progress Not Showing**
   - Check browser console for JavaScript errors
   - Ensure WebSocket connection is active
   - Refresh the page and try again

4. **Slow Upload Speed**
   - Network bandwidth between panel and game server
   - Consider using GitHub proxy instead if upload is bottleneck
   - Check if both servers are in same region/datacenter

5. **Temporary Files Not Cleaned Up**
   - Files should auto-delete after installation
   - Manual cleanup: `rm -rf /tmp/cs2_panel_proxy_{user_id}/`
   - Check panel server logs for cleanup errors

#### Original Troubleshooting

### Proxy Not Working

1. **Check Proxy URL Format - IMPORTANT!**
   - ✅ **CORRECT**: Enter only the base URL: `https://ghfast.top`
   - ❌ **WRONG**: Do NOT include the path: `https://ghfast.top/https://github.com`
   - ❌ **WRONG**: Do NOT omit protocol: `ghfast.top`
   - The system will automatically append GitHub URLs to your proxy base URL

2. **Test Proxy Manually**
   ```bash
   curl -I https://ghfast.top/https://github.com/roflmuffin/CounterStrikeSharp
   ```

3. **Check Server Logs**
   - Look for curl errors in installation logs
   - Verify proxy URL is being used

4. **Try Direct Connection**
   - Clear proxy configuration
   - Test if direct connection works
   - If direct works, issue is with proxy service

### Common Issues

1. **Proxy Service Down**
   - Try different proxy service
   - Use direct connection temporarily

2. **Slow Downloads**
   - Some proxy services may be slower than direct
   - Try different proxy service
   - Consider regional proxy services

3. **Certificate Errors**
   - Some proxies may have SSL issues
   - This is rare with major proxy services
   - Report to proxy service provider

## References

- GitHub Proxy Services:
  - https://ghfast.top (China)
  - https://ghproxy.com (China)
  - https://mirror.ghproxy.com (Global)

- Related Documentation:
  - [Plugin Installation Guide](../docs/PLUGIN_INSTALLATION.md)
  - [GitHub Plugin UI Guide](../docs/GITHUB_PLUGIN_UI.md)
  - [Server Configuration](../docs/SERVER_CONFIGURATION.md)
