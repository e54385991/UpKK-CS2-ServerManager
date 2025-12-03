# Plugin Market Enhancements - Implementation Summary

## Overview
This document summarizes the enhancements made to the Plugin Market feature to support version selection and selective extraction/upgrade functionality.

## Problem Statement (Original Request)
The user requested two key enhancements:

1. **版本选择支持** (Version Selection Support): Add ability to select specific plugin versions, similar to the GitHub plugin installation in the server tab
2. **选择性解压升级** (Selective Extraction/Upgrade): 
   - Analyze plugin package before installation
   - Allow users to select directories to exclude from extraction
   - When using selective extraction, skip automatic dependency installation
   - Provide clear notifications to users about this behavior

## Implementation Details

### 1. Version Selection

#### Backend Changes
**File**: `api/routes/plugin_market.py`

Added new endpoint:
```python
@router.get("/plugins/{plugin_id}/releases")
async def get_plugin_releases(...)
```
- Fetches available releases for a market plugin
- Supports server-specific GitHub proxy configuration
- Returns up to 10 recent releases (configurable)
- Filters out Windows-specific releases
- Returns release metadata including tag, name, assets, etc.

Updated install endpoint:
```python
@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    ...
    download_url: Optional[str] = Query(None, ...),
    ...
)
```
- Added `download_url` parameter for version selection
- Validates download URL is a valid GitHub releases URL
- Falls back to latest release if not specified

#### Frontend Changes
**File**: `templates/plugin_market.html`

Added UI components:
- Version selection dropdown (shown after server selection)
- Loads versions dynamically via API
- Displays release name with indicators:
  - "(Latest)" for most recent release
  - "[Pre-release]" for pre-release versions
- Default selection: Latest version

JavaScript functions:
- `onServerSelected()`: Triggered when server is selected, loads versions
- `loadVersions(serverId)`: Fetches and populates version dropdown

### 2. Selective Extraction/Upgrade

#### Backend Changes
**File**: `api/routes/plugin_market.py`

Added new endpoint:
```python
@router.get("/plugins/{plugin_id}/analyze-archive")
async def analyze_plugin_archive(...)
```
- Downloads and analyzes plugin archive structure
- Returns directory tree for user selection
- Supports all archive types (zip, tar.gz, 7z)
- Reuses existing `analyze_archive` function from github_plugins

Updated install endpoint parameters:
```python
@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    ...
    exclude_dirs: list[str] = Query(default=[], ...),
    install_dependencies: bool = Query(default=True, ...),
    ...
)
```
- `exclude_dirs`: Directories to exclude from extraction
- `install_dependencies`: Control automatic dependency installation
- When `exclude_dirs` is used, dependencies are NOT installed by default

#### Frontend Changes
**File**: `templates/plugin_market.html`

Added Advanced Options section:
- Collapsible accordion panel
- Clear warning message about dependency behavior
- "Analyze Plugin Package" button
- Directory tree with checkboxes for exclusion
- Loading indicator during analysis

JavaScript functions:
- `toggleAdvancedOptions()`: Shows/hides advanced panel
- `analyzeArchive()`: Analyzes plugin structure via API
- `displayDirectoryTree(directories)`: Renders directory checkboxes
- `getExcludedDirectories()`: Collects user selections
- Updated `submitInstallPlugin()`: Passes advanced options to API

### 3. Code Quality Improvements

#### Validation & Error Handling
- Added URL validation for `download_url` parameter
- Proper error handling for missing assets
- Safe array access with existence checks

#### Code Organization
- Extracted magic number to constant: `MAX_RELEASES_TO_FETCH = 10`
- Improved code comments for clarity
- Better separation of concerns

### 4. Internationalization (i18n)

#### Added Translation Keys
**Files**: `static/locales/en-US.json`, `static/locales/zh-CN.json`

New keys in `pluginMarket.installModal`:
- `selectVersion`: "Select Version" / "选择版本"
- `loadingVersions`: "Loading versions..." / "加载版本中..."
- `versionHelp`: "Latest version is selected by default" / "默认选择最新版本"
- `advancedOptions`: "Advanced Options (For Updates/Upgrades)" / "高级选项（用于更新/升级）"
- `advancedWarning`: "Warning:" / "警告："
- `advancedWarningText`: Full warning text about selective extraction / 完整的选择性解压警告文本
- `analyzePlugin`: "Analyze Plugin Package" / "分析插件包"
- `analyzing`: "Analyzing plugin package..." / "正在分析插件包..."
- `selectExclude`: "Select directories to EXCLUDE..." / "选择要排除安装的目录："
- `excludeHelp`: Help text for directory exclusion / 目录排除帮助文本

### 5. Documentation Updates

**File**: `docs/PLUGIN_MARKET.md`

Added sections:
- Documentation for new API endpoints with examples
- Standard installation flow (step-by-step)
- Advanced installation flow (step-by-step)
- Important notes about dependency behavior
- Recent updates section

## User Experience

### Standard Installation Flow
1. Click "Install" on a plugin
2. Select server from dropdown
3. Version dropdown loads automatically
4. Optionally select specific version (latest is default)
5. Click "Install"
6. Plugin installs with all dependencies

### Advanced Installation Flow (For Updates)
1. Click "Install" on a plugin
2. Select server from dropdown
3. Optionally select specific version
4. Enable "Advanced Options"
5. Click "Analyze Plugin Package"
6. Review directory structure
7. Check directories to EXCLUDE (e.g., configs)
8. Click "Install"
9. Plugin installs WITHOUT automatic dependencies
10. User sees clear message about dependency behavior

## Technical Benefits

### Version Selection
- ✅ Users can install specific stable versions
- ✅ Users can test pre-release versions
- ✅ Rollback to previous version if needed
- ✅ Consistent with GitHub plugin installation UX

### Selective Extraction
- ✅ Safe plugin updates without losing configs
- ✅ Granular control over installation
- ✅ Clear communication about dependency behavior
- ✅ Prevents config overwrites during upgrades

### Code Quality
- ✅ Proper input validation
- ✅ Safe array access
- ✅ Clear code comments
- ✅ Consistent with existing patterns

### Security
- ✅ URL validation for download URLs
- ✅ CodeQL scan: 0 alerts
- ✅ No new vulnerabilities introduced

## Files Modified

### Backend
- `api/routes/plugin_market.py` (+140 lines, modified install logic)

### Frontend
- `templates/plugin_market.html` (+170 lines, new UI components)

### Documentation
- `docs/PLUGIN_MARKET.md` (+105 lines, comprehensive updates)

### Localization
- `static/locales/en-US.json` (+10 keys)
- `static/locales/zh-CN.json` (+10 keys)

## Testing Recommendations

### Manual Testing
1. **Version Selection**
   - Verify version dropdown loads after server selection
   - Verify latest version is selected by default
   - Verify pre-release versions are marked
   - Verify installation works with selected version

2. **Selective Extraction**
   - Verify "Analyze Plugin Package" works
   - Verify directory tree displays correctly
   - Verify exclusions are applied during installation
   - Verify dependencies are NOT installed when using advanced mode
   - Verify warning message is displayed

3. **Internationalization**
   - Switch language to English and Chinese
   - Verify all new strings are translated
   - Verify no missing translation keys

4. **Edge Cases**
   - Plugin with no releases
   - Plugin with only pre-releases
   - Archive with no analyzable structure
   - Network errors during version fetch
   - Network errors during analysis

## Future Enhancements

Potential improvements:
- Show plugin changelog when selecting versions
- Remember user's version preference per plugin
- Bulk exclude patterns (e.g., "*.cfg")
- Visual diff of changes when updating
- Version comparison tool

## Conclusion

The plugin market enhancements successfully implement both requested features:
1. ✅ Version selection with intuitive UI
2. ✅ Selective extraction with clear warnings

The implementation maintains code quality, adds proper i18n support, and follows existing patterns in the codebase. All security checks pass with 0 alerts.
