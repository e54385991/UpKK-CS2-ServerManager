# Plugin Management UI - Visual Guide

## Tab Location

The "Plugins" tab appears in the server detail page navigation bar, between "Scheduled Tasks" and "Help" tabs.

```
[Overview] [Configuration] [Actions] [Monitoring & Restart] [Console & Logs] [File Manager] [Scheduled Tasks] [Plugins] [Help]
                                                                                                               ^^^^^^^^^
                                                                                                               New Tab!
```

Icon: 🧩 (Puzzle piece - Bootstrap icon: `bi-puzzle`)

## Layout Structure

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ CounterStrikeSharp Plugins                                      [Refresh Button]    │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────────┐  ┌──────────────────────────────────────────────────────┐    │
│  │ Categories      │  │ Plugin Grid (2 columns on desktop, 1 on mobile)       │    │
│  ├─────────────────┤  │                                                        │    │
│  │ ● All Plugins   │  │  ┌────────────────┐  ┌────────────────┐             │    │
│  │   [17]          │  │  │ Admin Mgmt     │  │ Teleport Mgr   │             │    │
│  │                 │  │  │ [Admin] v1.2.0 │  │ [Utility] v1.0.5│             │    │
│  │ ○ Utility       │  │  │ Comprehensive  │  │ Teleport       │             │    │
│  │ ○ Gameplay      │  │  │ admin...       │  │ players...     │             │    │
│  │ ○ Admin         │  │  │                │  │ ⚠️ Dependencies│             │    │
│  │ ○ Chat          │  │  │ v1.2.0         │  │ v1.0.5         │             │    │
│  │ ○ Statistics    │  │  │ By CS2 Comm... │  │ By TeleportDev │             │    │
│  │ ○ Cosmetic      │  │  │ [Install]      │  │ [Install]      │             │    │
│  │ ○ Other         │  │  │ [Homepage]     │  │ [Homepage]     │             │    │
│  │                 │  │  └────────────────┘  └────────────────┘             │    │
│  ├─────────────────┤  │                                                        │    │
│  │ Installed       │  │  ┌────────────────┐  ┌────────────────┐             │    │
│  │ Plugins         │  │  │ Player Stats   │  │ Enhanced Chat  │             │    │
│  ├─────────────────┤  │  │ [Stats] v2.1.0 │  │ [Chat] v1.5.2  │             │    │
│  │ ○ Admin Mgmt    │  │  │ Track player   │  │ Enhanced chat  │             │    │
│  │   v1.2.0 [🗑️]   │  │  │ statistics...  │  │ with colors... │             │    │
│  │                 │  │  │ v2.1.0         │  │ v1.5.2         │             │    │
│  │                 │  │  │ By StatsTeam   │  │ By ChatMods    │             │    │
│  │                 │  │  │ [Install]      │  │ [Install]      │             │    │
│  └─────────────────┘  │  │ [Homepage]     │  │ [Homepage]     │             │    │
│                       │  └────────────────┘  └────────────────┘             │    │
│                       │                                                        │    │
│                       │  [← Previous] [1] [2] [3] [Next →]                    │    │
│                       └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Plugin Card Details

Each plugin card displays:

```
┌──────────────────────────────────┐
│ Plugin Display Name       [BADGE]│  ← Badge color indicates category
│                                   │
│ Brief description of what the     │
│ plugin does, truncated if needed  │
│                                   │
│ 🏷️ Version: 1.2.0                │
│ 👤 Author Name                    │
│                                   │
│ ⚠️ Has dependencies (optional)    │  ← Only shown if plugin has dependencies
│                                   │
│ [   Install   ]                   │  ← Green button
│ [ Homepage 🔗 ]                   │  ← Opens in new tab (if available)
└──────────────────────────────────┘

OR (if installed):

┌──────────────────────────────────┐
│ Plugin Display Name       [BADGE]│  ← Card has green border
│                                   │
│ Brief description...              │
│                                   │
│ 🏷️ Version: 1.2.0                │
│ 👤 Author Name                    │
│                                   │
│ [ ✓ Installed ]                   │  ← Green outline button (disabled)
│ [ Homepage 🔗 ]                   │
└──────────────────────────────────┘
```

## Category Colors

- **Utility**: Blue (info)
- **Gameplay**: Dark Blue (primary)
- **Admin**: Red (danger)
- **Chat**: Green (success)
- **Statistics**: Yellow (warning)
- **Cosmetic**: Gray (secondary)
- **Other**: Dark Gray (dark)

## Install Modal

When clicking "Install":

```
┌─────────────────────────────────────────┐
│ 🔽 Install Plugin              [✕]      │
├─────────────────────────────────────────┤
│                                         │
│ Plugin Display Name                     │
│ Full description of the plugin here     │
│ explaining what it does in detail.      │
│                                         │
│ Custom Download URL (Optional)          │
│ ┌─────────────────────────────────────┐ │
│ │ https://...                         │ │
│ └─────────────────────────────────────┘ │
│ Leave empty to use default URL          │
│                                         │
│ Configuration (JSON)                    │
│ ┌─────────────────────────────────────┐ │
│ │ {"key": "value"}                    │ │
│ │                                     │ │
│ └─────────────────────────────────────┘ │
│ Enter plugin configuration in JSON      │
│                                         │
│ ⚠️ This plugin has dependencies that    │
│    will be installed automatically      │
│                                         │
│              [Cancel] [Install]         │
└─────────────────────────────────────────┘
```

## Installed Plugins Sidebar

```
┌─────────────────┐
│ Installed       │
│ Plugins         │
├─────────────────┤
│ ○ Admin Mgmt    │
│   v1.2.0    [🗑️]│  ← Click trash to uninstall
│                 │
│ ○ Stats         │
│   v2.1.0    [🗑️]│
│                 │
└─────────────────┘
```

## Color Scheme

- **Background**: Light gray (#f8f9fa)
- **Cards**: White with subtle shadow
- **Card hover**: Slight elevation (translateY)
- **Active category**: Blue background (#0d6efd)
- **Installed card border**: Green (2px solid)
- **Success toast**: Green background
- **Error toast**: Red background

## Icons Used

- Tab icon: `bi-puzzle` (Puzzle piece)
- Categories icon: `bi-funnel` (Funnel)
- Installed icon: `bi-check-circle` (Check circle)
- Refresh icon: `bi-arrow-clockwise`
- Install icon: `bi-download`
- Installed status: `bi-check-circle`
- Uninstall icon: `bi-trash`
- Homepage icon: `bi-box-arrow-up-right`
- Dependency warning: `bi-exclamation-triangle`
- Version tag: `bi-tag`
- Author: `bi-person`

## Responsive Behavior

### Desktop (≥768px)
- 2 columns of plugin cards
- Sidebar always visible
- Modal width: ~500px

### Mobile (<768px)
- 1 column of plugin cards
- Sidebar scrollable
- Modal full width with padding

## Interactions

1. **Category Click**: Filters plugins, highlights category, resets pagination
2. **Plugin Install**: Opens modal → Shows form → Install button starts loading
3. **Install Success**: Modal closes → Toast appears → Installed list updates
4. **Plugin Uninstall**: Confirmation dialog → Remove → Toast → List updates
5. **Pagination**: Loads new page, scrolls to top
6. **Refresh**: Reloads all data (categories, plugins, installed)

## Loading States

- **Initial load**: Spinner in center of plugin grid area
- **Installing**: "Installing..." on button with spinner icon
- **Refreshing**: Spinner icon on refresh button

## Empty States

- **No plugins**: "No plugins found" message
- **No installed**: "No plugins installed" in sidebar
- **No category match**: "No plugins in this category"

## Toast Notifications

Success (green):
```
✓ Plugin installed successfully
✓ Admin Management uninstalled successfully
```

Error (red):
```
✗ Failed to install plugin
✗ Plugin is already installed on this server
```

## Accessibility

- All buttons have aria-labels
- Modal has proper focus management
- Keyboard navigation supported
- Screen reader friendly labels
- Color contrast meets WCAG AA standards

## Performance

- Pagination prevents loading all plugins at once
- Category filtering is server-side (fast)
- Installed plugins cached per server
- Cards use CSS transforms for smooth animations
- Lazy loading for plugin descriptions
