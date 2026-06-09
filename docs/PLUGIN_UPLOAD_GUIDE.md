# Plugin Upload Feature - Visual Guide

## Upload Button Location

The "Upload Plugin" button appears in the Plugins tab header, but **only for administrators**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ CounterStrikeSharp Plugins    [Upload Plugin] [Refresh]                     │
│                                      ^                                       │
│                                      |                                       │
│                           (Only visible for admins)                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

Icon: 🔼 (Cloud upload - Bootstrap icon: `bi-cloud-upload`)
Color: Primary blue button

## Upload Modal

When clicking "Upload Plugin":

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🔼 Upload Plugin                                            [✕]     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ Plugin File (.tar.gz) *                                             │
│ ┌─────────────────────────────────────────────────────────────┐   │
│ │ [Choose File] my_plugin_1.0.0.tar.gz                        │   │
│ └─────────────────────────────────────────────────────────────┘   │
│ Upload a tar.gz archive containing your plugin                     │
│                                                                     │
│ ┌──────────────────────────┬──────────────────────────┐          │
│ │ Plugin Name *            │ Display Name *           │          │
│ │ ┌──────────────────────┐ │ ┌──────────────────────┐ │          │
│ │ │ my_plugin            │ │ │ My Awesome Plugin    │ │          │
│ │ └──────────────────────┘ │ └──────────────────────┘ │          │
│ │ Alphanumeric, dashes,    │                          │          │
│ │ and underscores only     │                          │          │
│ └──────────────────────────┴──────────────────────────┘          │
│                                                                     │
│ Description *                                                       │
│ ┌─────────────────────────────────────────────────────────────┐   │
│ │ This plugin adds awesome features to your CS2 server...     │   │
│ │                                                              │   │
│ └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│ ┌──────────────┬──────────────┬──────────────┐                   │
│ │ Category *   │ Version *    │ Author       │                   │
│ │ ┌──────────┐ │ ┌──────────┐ │ ┌──────────┐ │                   │
│ │ │ Utility ▼│ │ │ 1.0.0    │ │ │ Your Name│ │                   │
│ │ └──────────┘ │ └──────────┘ │ └──────────┘ │                   │
│ └──────────────┴──────────────┴──────────────┘                   │
│                                                                     │
│ Homepage URL                                                        │
│ ┌─────────────────────────────────────────────────────────────┐   │
│ │ https://github.com/username/plugin                          │   │
│ └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│ Install Path                                                        │
│ ┌─────────────────────────────────────────────────────────────┐   │
│ │ addons/counterstrikesharp/plugins                           │   │
│ └─────────────────────────────────────────────────────────────┘   │
│ Relative to game/csgo/ directory                                   │
│                                                                     │
│ ☐ This plugin requires configuration                              │
│                                                                     │
│                                    [Cancel]  [Upload]              │
└─────────────────────────────────────────────────────────────────────┘
```

## Form Fields

### Required Fields (marked with *)
1. **Plugin File** - File input, accepts only .tar.gz
2. **Plugin Name** - Text input with pattern validation (alphanumeric + `-_`)
3. **Display Name** - User-friendly name
4. **Description** - Multi-line text area
5. **Category** - Dropdown with 7 categories
6. **Version** - Version string (e.g., 1.0.0)

### Optional Fields
7. **Author** - Plugin author name
8. **Homepage** - URL to plugin homepage/repository
9. **Install Path** - Defaults to `addons/counterstrikesharp/plugins`
10. **Configuration Required** - Checkbox

## Upload Flow

```
User clicks "Upload Plugin"
           ↓
Modal opens with empty form
           ↓
User fills in information
           ↓
User selects .tar.gz file
           ↓
User clicks "Upload"
           ↓
      Validation
           ↓
    ┌──────┴──────┐
    │             │
 Valid         Invalid
    │             │
    ↓             ↓
Upload        Show error
to server     message
    │
    ↓
File saved to
static/uploads/plugins/
    │
    ↓
Database entry created
with local URL path
    │
    ↓
Success toast shown
    │
    ↓
Modal closes
    │
    ↓
Plugin list refreshes
    │
    ↓
New plugin appears
in catalog
```

## After Upload

The uploaded plugin appears in the catalog immediately:

```
┌────────────────────────────────────────┐
│ My Awesome Plugin          [UTILITY]   │
│                                         │
│ This plugin adds awesome features to   │
│ your CS2 server...                     │
│                                         │
│ 🏷️ Version: 1.0.0                      │
│ 👤 Your Name                            │
│                                         │
│ [   Install   ]                         │
│ [ Homepage 🔗 ]                         │
└────────────────────────────────────────┘
```

Download URL is set to: `/static/uploads/plugins/my_plugin_1.0.0.tar.gz`

When users click "Install" on this plugin, the system will:
1. Download from the local static URL
2. Install to the server via SSH
3. Track installation in database

## Permissions

### Admin Users
- See "Upload Plugin" button
- Can upload new plugins
- Can create plugin entries in catalog

### Regular Users
- Cannot see "Upload Plugin" button
- Can only install existing plugins
- Cannot add to catalog

## File Storage

```
/static/uploads/plugins/
├── .gitkeep                           # Tracked in git
├── admin_management_1.2.0.tar.gz      # Ignored by git
├── teleport_manager_1.0.5.tar.gz      # Ignored by git
└── my_plugin_1.0.0.tar.gz             # Ignored by git
```

Files are served by the FastAPI static file handler at `/static/uploads/plugins/{filename}`

## Validation Rules

### File Validation
- Must end with `.tar.gz`
- Checked both client-side (HTML accept attribute) and server-side

### Name Validation
- Pattern: `^[a-zA-Z0-9_-]+$`
- Only alphanumeric characters, dashes, and underscores
- Used for directory names and URLs

### Category Validation
- Must be one of 7 valid categories:
  - utility
  - gameplay
  - admin
  - chat
  - statistics
  - cosmetic
  - other

## Security Features

✅ **Admin-only access** - Upload button only visible to admins  
✅ **Server-side validation** - All inputs validated on backend  
✅ **File type check** - Only .tar.gz files accepted  
✅ **Name sanitization** - Safe characters only  
✅ **Path sanitization** - Clean filenames generated  
✅ **Error handling** - Failed uploads cleaned up  
✅ **Git ignored** - Uploaded files not committed to repo

## Toast Notifications

**Success:**
```
✓ Plugin My Awesome Plugin uploaded successfully!
```

**Error Examples:**
```
✗ Plugin file must be a .tar.gz archive
✗ Plugin name must contain only alphanumeric characters, dashes, and underscores
✗ Invalid category. Must be one of: utility, gameplay, admin, chat, statistics, cosmetic, other
✗ Failed to upload plugin: [error message]
```

## Button States

### Normal State
```
[Upload Plugin]
```

### During Upload
```
[↻ Uploading...]
```
(Spinner icon rotates, button disabled)

## Integration with Existing Features

1. **Uploaded plugins appear in catalog** - Same as URL-based plugins
2. **Can be filtered by category** - Works with existing filters
3. **Appear in search results** - If search is implemented
4. **Can be installed on servers** - Same installation flow
5. **Support dependencies** - Can specify dependency IDs
6. **Support configuration** - Can mark as requiring config

## Advantages of Upload Feature

1. **No external hosting needed** - Upload directly to manager
2. **Private plugins** - Keep custom plugins internal
3. **Quick testing** - Upload and test immediately
4. **Version control** - Upload new versions easily
5. **Offline support** - No internet required for installation
6. **Full control** - Manage entire plugin lifecycle
7. **Centralized storage** - All plugins in one place

## Example Upload Scenario

**Admin wants to add a custom private plugin:**

1. Developer creates plugin and packages as `custom_plugin_1.0.0.tar.gz`
2. Admin logs into server manager
3. Goes to any server's Plugins tab
4. Clicks "Upload Plugin"
5. Fills in form:
   - Name: `custom_plugin`
   - Display Name: "Custom Plugin"
   - Description: "Our team's custom plugin"
   - Category: "Utility"
   - Version: "1.0.0"
   - Author: "Internal Team"
   - Selects the .tar.gz file
6. Clicks "Upload"
7. Plugin is now available in catalog
8. Can be installed on any managed server
9. Updates by uploading new version with incremented version number

This makes the plugin management system completely self-contained!
