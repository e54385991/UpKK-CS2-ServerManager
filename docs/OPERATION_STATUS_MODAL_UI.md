# Operation Status Modal - UI Reference

## Modal Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  Operation Status: Deploy Server                          [×]   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ Terminal Output Area (500px height)                      │ │
│  │                                                           │ │
│  │ [12:34:56] Connected to operation monitor                │ │
│  │ [12:34:57] Starting action: deploy...                    │ │
│  │ [12:34:58] Connecting to server via SSH...               │ │
│  │ [12:35:00] SSH connection established                    │ │
│  │ [12:35:01] Checking SteamCMD installation...             │ │
│  │ [12:35:02] SteamCMD found at /home/steam/steamcmd        │ │
│  │ [12:35:03] Starting CS2 installation...                  │ │
│  │ [12:35:05] Downloading game files (this may take time)   │ │
│  │ [12:35:10] Progress: 5% complete                         │ │
│  │ [12:35:15] Progress: 15% complete                        │ │
│  │ ...                                                       │ │
│  │ [12:45:30] Download complete                             │ │
│  │ [12:45:31] Deployment completed successfully             │ │
│  │                                                           │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  ✓ Operation completed                              [Close]    │
└─────────────────────────────────────────────────────────────────┘
```

## Color Scheme

### Message Types and Colors

1. **Info Messages** (white/light gray)
   - General information
   - Connection status
   - Example: "Connected to operation monitor"

2. **Status Messages** (blue - text-info)
   - Operation status updates
   - Progress indicators
   - Example: "Starting action: deploy..."
   - Example: "Connecting to server via SSH..."

3. **Output Messages** (white - text-light)
   - Command output from SSH
   - Server responses
   - Example: "SteamCMD found at /home/steam/steamcmd"

4. **Success/Complete Messages** (green - text-success)
   - Operation completion
   - Successful steps
   - Example: "Deployment completed successfully"
   - Example: "Server started successfully"

5. **Error Messages** (red - text-danger)
   - Operation failures
   - Error details
   - Example: "SSH connection failed: Authentication error"
   - Example: "Deployment failed: Insufficient disk space"

6. **Warning Messages** (yellow - text-warning)
   - Non-critical issues
   - Important notices
   - Example: "Server already running, skipping start"

## Footer States

### State 1: Operation In Progress
```
├─────────────────────────────────────────────────────────────────┤
│  ◌ Operation in progress...                      [Close] 🚫    │
└─────────────────────────────────────────────────────────────────┘
```
- Spinner animation (◌ rotating)
- Close button disabled
- Gray text

### State 2: Operation Completed (Success)
```
├─────────────────────────────────────────────────────────────────┤
│  ✓ Operation completed                              [Close]    │
└─────────────────────────────────────────────────────────────────┘
```
- Green checkmark icon
- Close button enabled
- Green text

### State 3: Operation Failed (Error)
```
├─────────────────────────────────────────────────────────────────┤
│  ✗ Operation failed                                 [Close]    │
└─────────────────────────────────────────────────────────────────┘
```
- Red X icon
- Close button enabled
- Red text

## Example Scenarios

### Example 1: Starting a Server
```
┌─────────────────────────────────────────────────────────────────┐
│  Operation Status: Start                                  [×]   │
├─────────────────────────────────────────────────────────────────┤
│  [12:00:01] Connected to operation monitor                     │
│  [12:00:02] Starting server...                                 │
│  [12:00:03] Checking for existing screen sessions...           │
│  [12:00:04] Starting CS2 server in screen session...           │
│  [12:00:05] Screen session created: cs2server_123              │
│  [12:00:06] Server process started                             │
│  [12:00:07] Waiting for server to initialize...                │
│  [12:00:10] Server is now running                              │
│  [12:00:11] Server started successfully                        │
├─────────────────────────────────────────────────────────────────┤
│  ✓ Operation completed                              [Close]    │
└─────────────────────────────────────────────────────────────────┘
```

### Example 2: Batch Plugin Installation
```
┌─────────────────────────────────────────────────────────────────┐
│  Operation Status: Install Metamod:Source                 [×]   │
├─────────────────────────────────────────────────────────────────┤
│  [13:00:01] Connected to operation monitor                     │
│  [13:00:02] --- Starting: Install Metamod:Source ---           │
│  [13:00:03] Installing Metamod:Source...                       │
│  [13:00:04] Downloading latest release from GitHub...          │
│  [13:00:10] Download complete                                  │
│  [13:00:11] Extracting files...                                │
│  [13:00:12] Metamod installed successfully                     │
│  [13:00:13] --- Starting: Install CounterStrikeSharp ---       │
│  [13:00:14] Installing CounterStrikeSharp...                   │
│  [13:00:15] Downloading latest release from GitHub...          │
│  [13:00:22] Download complete                                  │
│  [13:00:23] Extracting files...                                │
│  [13:00:24] CounterStrikeSharp installed successfully          │
│  [13:00:25] --- Starting: Install CS2Fixes ---                 │
│  [13:00:26] Installing CS2Fixes...                             │
│  [13:00:27] Downloading latest release from GitHub...          │
│  [13:00:33] Download complete                                  │
│  [13:00:34] Extracting files...                                │
│  [13:00:35] CS2Fixes installed successfully                    │
├─────────────────────────────────────────────────────────────────┤
│  ✓ Operation completed                              [Close]    │
└─────────────────────────────────────────────────────────────────┘
```

### Example 3: Error Scenario
```
┌─────────────────────────────────────────────────────────────────┐
│  Operation Status: Deploy Server                          [×]   │
├─────────────────────────────────────────────────────────────────┤
│  [14:00:01] Connected to operation monitor                     │
│  [14:00:02] Starting action: deploy...                         │
│  [14:00:03] Connecting to server via SSH...                    │
│  [14:00:05] SSH connection failed: Connection timeout          │
│  [14:00:06] Deployment failed: Unable to connect to server     │
├─────────────────────────────────────────────────────────────────┤
│  ✗ Operation failed                                 [Close]    │
└─────────────────────────────────────────────────────────────────┘
```

## Modal Behavior

### Opening Behavior
- Modal fades in smoothly (Bootstrap default)
- Backdrop appears (static - cannot click to dismiss)
- Modal centers on screen
- Terminal area is empty initially
- WebSocket connection establishes within 1 second
- First message: "Connected to operation monitor"

### During Operation
- Messages appear in real-time as received from WebSocket
- Each message prefixed with timestamp [HH:MM:SS]
- Terminal auto-scrolls to show latest message
- User can manually scroll up to read previous messages
- Close button (×) is disabled
- Backdrop click does nothing
- ESC key does nothing

### After Completion
- Final message displayed ("completed successfully" or error)
- Footer updates to show completion state
- Close button becomes enabled
- User can review full log before closing
- Clicking Close or × closes modal
- WebSocket connection is terminated
- Modal state resets for next operation

## Responsive Design

### Desktop (>992px)
- Modal width: Large (modal-lg ~800px)
- Terminal height: 500px
- Fully readable with standard font sizes

### Tablet (768px - 992px)
- Modal adapts to smaller width
- Terminal height: 500px (same)
- Scrollable if content exceeds width

### Mobile (<768px)
- Modal takes most of screen width
- Terminal height: 400px (slightly reduced)
- Font size may be slightly smaller
- Touch-scrolling enabled

## Accessibility

### Keyboard Navigation
- Tab to Close button (when enabled)
- Enter/Space to activate Close button
- ESC key disabled during operation (by design)
- ESC key closes modal after completion

### Screen Readers
- Modal title announced when opened
- Status changes announced
- ARIA labels on interactive elements
- Semantic HTML structure

### Color Blind Friendly
- Icons supplement colors (✓ ✗ ◌)
- Text descriptions accompany color states
- High contrast between text and background

## Technical CSS Classes

```css
/* Modal Structure */
.modal-dialog.modal-lg    /* Modal width */
.modal-header            /* Title bar */
.modal-body.p-0          /* Content area (no padding) */
.modal-footer            /* Footer with status and close button */

/* Terminal Area */
#operationTerminal       /* Terminal container */
  background-color: #1e1e1e
  color: #d4d4d4
  font-family: 'Courier New', monospace
  font-size: 14px
  height: 500px
  overflow-y: auto

/* Message Types */
.text-info              /* Blue - status messages */
.text-success           /* Green - success messages */
.text-danger            /* Red - error messages */
.text-warning           /* Yellow - warning messages */
.text-light             /* White - output/info messages */
.text-muted             /* Gray - timestamps */

/* Footer States */
.spinner-border          /* Loading spinner */
.bi-check-circle        /* Success icon */
.bi-x-circle            /* Error icon */
```

## Integration Points

### Triggers
- All action buttons in "Actions" tab
- Individual plugin install/update buttons
- Batch plugin installation button
- executeAction() function

### WebSocket Endpoint
- URL: `ws://host/servers/{server_id}/deployment-status`
- Protocol: WebSocket (wss:// for HTTPS)
- Message format: JSON with type and message fields

### Data Flow
```
User Action → executeAction() → openOperationModal() → Show Modal
                    ↓
            POST /servers/{id}/actions
                    ↓
            Backend Processing → Send WebSocket Messages
                    ↓
            onmessage Handler → addOperationMessage()
                    ↓
            Update Modal Display → Update Footer State
                    ↓
            Operation Complete → Enable Close Button
```
