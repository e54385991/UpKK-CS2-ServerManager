# Plugin Upload Feature - Quick Reference

## What Was Added

In response to the request "插件能通过webui上传添加" (Plugins can be uploaded and added through the web UI), we implemented a complete file upload system for CounterStrikeSharp plugins.

## Commits

- **54ac21e** - Add plugin upload functionality via web UI
- **22adabc** - Add plugin upload visual guide documentation

## Files Modified/Created

### Backend
- `api/routes/plugins.py` - Added `/upload` endpoint
- `api/routes/auth.py` - Added `/me` endpoint for user info

### Frontend
- `templates/server_detail_includes/plugins_tab.html` - Added upload button and modal
- `templates/server_detail_includes/scripts.html` - Added upload functions

### Infrastructure
- `static/uploads/plugins/` - Created upload directory
- `.gitignore` - Added rule to ignore uploaded files

### Documentation
- `docs/PLUGIN_UPLOAD_GUIDE.md` - Complete visual guide

## Key Features

1. **Admin-Only Upload Button**
   - Visible only to administrators
   - Located in Plugins tab header
   - Primary blue color, cloud-upload icon

2. **Comprehensive Upload Form**
   - File input (accepts .tar.gz only)
   - Plugin metadata fields:
     - Name (validated pattern)
     - Display name
     - Description
     - Category (dropdown)
     - Version
     - Author (optional)
     - Homepage (optional)
     - Install path (with default)
     - Configuration required (checkbox)

3. **File Storage**
   - Uploads saved to `static/uploads/plugins/`
   - Served via FastAPI static files
   - Download URL: `/static/uploads/plugins/{filename}`
   - Files ignored by git

4. **Integration**
   - Uploaded plugins appear in catalog immediately
   - Can be installed like any other plugin
   - Supports all existing features (dependencies, config, etc.)

## API Endpoints

```
POST /api/plugins/upload
- Multipart form data
- Requires admin authentication
- Validates file type and plugin metadata
- Returns PluginResponse

GET /api/auth/me
- Returns current user information
- Used to check if user is admin
```

## Security

✅ Admin-only access (button + API)  
✅ File type validation (.tar.gz)  
✅ Name pattern validation  
✅ Category validation  
✅ Secure filename generation  
✅ Error handling with cleanup  
✅ Git ignored uploads  

## Usage Example

```javascript
// Admin user flow
1. Click "Upload Plugin"
2. Fill form:
   - Select file: my_plugin.tar.gz
   - Name: my_plugin
   - Display: "My Plugin"
   - Description: "Custom plugin"
   - Category: "utility"
   - Version: "1.0.0"
3. Click "Upload"
4. See success message
5. Plugin appears in catalog
6. Install on servers
```

## Benefits

- **No external hosting required**
- **Private plugin support**
- **Immediate availability**
- **Offline capability**
- **Centralized management**
- **Easy version updates**

## Testing

To test:
1. Log in as admin user
2. Navigate to any server's Plugins tab
3. Verify "Upload Plugin" button is visible
4. Click button and verify modal opens
5. Fill in form and upload a test .tar.gz file
6. Verify plugin appears in catalog
7. Try installing the uploaded plugin

## 中文说明

**实现了什么：**
- 管理员可以通过Web界面上传插件文件
- 支持 .tar.gz 压缩包格式
- 上传后立即可用，无需外部托管

**如何使用：**
1. 管理员登录
2. 进入"插件"标签
3. 点击"Upload Plugin"按钮
4. 填写插件信息并选择文件
5. 点击上传
6. 插件自动添加到目录

**优势：**
- 私有插件支持
- 无需互联网
- 集中管理
- 即时可用

**安全性：**
- 仅管理员可见和使用
- 文件类型验证
- 输入验证
- 自动清理错误上传

完整实现，可立即使用！
