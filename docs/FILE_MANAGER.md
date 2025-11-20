# File Manager Feature

## Overview

The File Manager feature provides a comprehensive web-based interface for managing files on your CS2 servers. This feature allows you to browse, edit, upload, download, and manage files directly through the web interface without needing SSH access.

## Features

### File Operations
- **Browse Files**: Navigate through directories with a user-friendly interface
- **Upload Files**: Upload single or multiple files with drag-and-drop support
- **Download Files**: Download files from the server to your local machine
- **Edit Files**: Edit text-based configuration files inline with syntax highlighting
- **Create Folders**: Create new directories on the server
- **Rename**: Rename files and folders
- **Delete**: Remove files and folders (with confirmation)

### User Interface
- **Breadcrumb Navigation**: Easy navigation through directory hierarchy
- **File Type Icons**: Visual indicators for different file types
- **File Metadata**: Display file size, modification time, and permissions
- **Responsive Design**: Works on desktop and mobile devices
- **Internationalization**: Full support for English and Chinese

## Security

The File Manager includes several security features:

1. **Authentication Required**: All file operations require user authentication
2. **Path Traversal Protection**: Users cannot access files outside the server's game directory
3. **Ownership Verification**: Users can only access files on their own servers
4. **SFTP Protocol**: Secure file transfer using SSH/SFTP

## Usage

### Accessing the File Manager

1. Navigate to your server's detail page
2. Click on the "File Manager" tab
3. The file browser will open showing your server's game directory

### Browsing Files

- Click on a folder name or icon to navigate into it
- Click on the breadcrumb trail to navigate back to parent directories
- Click the "Parent Directory" link to go up one level
- Double-click on a file to open it for editing (text files only)

### Uploading Files

1. Click the "Upload" button in the toolbar
2. Select one or more files from your computer
3. Files will be uploaded to the current directory

### Editing Files

1. Click the edit icon (pencil) next to a text file
2. Or double-click on a text file
3. Make your changes in the editor
4. Click "Save" to save changes or "Cancel" to discard

Supported file types for editing:
- Configuration files (.cfg, .conf, .ini, .yaml, .yml, .toml, .properties)
- Log files (.log, .txt)
- Script files (.sh, .bash, .py, .js, .lua)
- Web files (.html, .css, .json, .xml)
- Source code (.c, .cpp, .h, .java, .cs, .go, .rs)

### Downloading Files

1. Click the download icon next to any file
2. The file will be downloaded to your browser's download folder

### Creating Folders

1. Click the "New Folder" button in the toolbar
2. Enter the folder name
3. Click "Create"

### Renaming Files/Folders

1. Click the rename icon next to the file or folder
2. Enter the new name
3. Click "Rename"

### Deleting Files/Folders

1. Click the delete icon (trash) next to the file or folder
2. Confirm the deletion in the popup dialog
3. The item will be permanently deleted

## Technical Details

### Backend API

The file manager uses the following API endpoints:

- `GET /servers/{id}/files` - List directory contents
- `GET /servers/{id}/files/content` - Get file content
- `PUT /servers/{id}/files/content` - Update file content
- `POST /servers/{id}/files/upload` - Upload file
- `GET /servers/{id}/files/download` - Download file
- `POST /servers/{id}/files/mkdir` - Create directory
- `DELETE /servers/{id}/files` - Delete file/directory
- `POST /servers/{id}/files/rename` - Rename/move file

### File Transfer Protocol

All file operations use SFTP (SSH File Transfer Protocol) over the existing SSH connection to your server. This ensures secure and encrypted file transfers.

### Limitations

- Maximum file size for editing: 10 MB (configurable)
- Binary files cannot be edited, only downloaded
- File uploads are subject to browser and server memory limits

## Common Use Cases

### Editing Server Configuration
1. Navigate to `/cs2/game/csgo/cfg/`
2. Edit `server.cfg` to modify server settings
3. Save changes
4. Restart server for changes to take effect

### Managing Plugins
1. Navigate to `/cs2/game/csgo/addons/counterstrikesharp/plugins/`
2. Upload plugin files
3. Edit plugin configuration files as needed

### Viewing Logs
1. Navigate to `/cs2/game/csgo/`
2. Open `console.log` to view server output
3. Download logs for offline analysis

### Backing Up Configurations
1. Navigate to configuration directories
2. Download important config files
3. Keep local backups

## Troubleshooting

### Cannot See Files
- Ensure the server is deployed and the directory exists
- Check that you have the correct permissions
- Refresh the file list using the refresh button

### Upload Failed
- Check file size limits
- Ensure you have write permissions in the directory
- Check available disk space on the server

### Edit/Save Failed
- Ensure the file is a text file
- Check file permissions
- Ensure the file is not locked by another process

### Connection Issues
- Verify SSH credentials are correct
- Check server SSH port is accessible
- Ensure SSH service is running on the server

## Best Practices

1. **Always Backup**: Download important configuration files before making changes
2. **Test Changes**: Test configuration changes on a test server first
3. **Use Version Control**: Keep track of configuration changes
4. **Check Permissions**: Ensure files have appropriate permissions
5. **Regular Cleanup**: Periodically clean up old log files and backups

## Security Best Practices

1. **Use Strong Passwords**: Ensure SSH passwords are strong
2. **Limit Access**: Only grant file manager access to trusted users
3. **Regular Audits**: Review file access logs regularly
4. **Minimal Permissions**: Use the principle of least privilege
5. **Secure Connection**: Always use HTTPS for the web interface

## Future Enhancements

Potential future improvements:
- Syntax highlighting in the code editor
- File search functionality
- Bulk operations (select multiple files)
- File compression/decompression
- File preview for images
- Drag-and-drop file upload
- Context menu (right-click) operations
