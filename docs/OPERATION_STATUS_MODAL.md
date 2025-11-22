# Operation Status Modal Feature

## Overview

The Operation Status Modal provides real-time feedback for server operations without requiring users to switch tabs. When any server action is executed (start, stop, restart, deploy, update, validate, or plugin installation), a modal window automatically opens and displays live operation status.

## Features

- **Non-blocking UI**: Operations run asynchronously without freezing the web interface
- **Real-time Updates**: WebSocket connection provides live output as operations execute
- **Terminal-style Display**: Output is shown in a console-like format with color-coded messages
- **Auto-scroll**: Automatically scrolls to show the latest messages
- **Operation Status Indicators**: Shows spinner during operation, success/failure icons when complete
- **Prevention of Accidental Closure**: Modal cannot be dismissed while operation is in progress
- **Internationalization**: Fully supports English and Chinese languages

## User Interface

### Modal Components

1. **Header**
   - Shows the operation being performed (e.g., "Deploy Server", "Start", "Install Metamod")
   - Close button (disabled during operation)

2. **Body**
   - Terminal-style output area (500px height)
   - Color-coded messages:
     - **Blue** (text-info): Status updates
     - **Green** (text-success): Completion messages
     - **Red** (text-danger): Error messages
     - **Yellow** (text-warning): Warning messages
     - **White** (text-light): General output and info

3. **Footer**
   - Left side: Operation status (in progress/completed/failed) with icon
   - Right side: Close button

## Supported Operations

All server actions automatically use the Operation Status Modal:

- **Server Management**: Deploy, Start, Stop, Restart, Update, Update+Validate, Check Status
- **Plugin Frameworks**: 
  - Install/Update Metamod:Source
  - Install/Update CounterStrikeSharp
  - Install/Update CS2Fixes

## Technical Implementation

### WebSocket Connection

The modal connects to the existing deployment status WebSocket endpoint:
```
/servers/{server_id}/deployment-status
```

This endpoint is already implemented in the backend and provides real-time updates for all server operations.

### Message Types

The modal handles the following WebSocket message types:
- `status`: Operation status updates
- `output`: Command output from SSH operations
- `complete`: Operation completed successfully
- `error`: Operation failed or error occurred
- `warning`: Warning messages
- `info`: Informational messages
- `ack`: Acknowledgment (ignored by modal)

### Auto-cleanup

- WebSocket connection is automatically closed when modal is dismissed
- Modal state is reset when closed
- Messages are cleared to prevent memory buildup

## Code Structure

### Alpine.js Data Properties

```javascript
operationMessages: [],        // Array of messages to display
currentOperationName: '',     // Friendly name of current operation
operationInProgress: false,   // Whether operation is running
operationCompleted: false,    // Whether operation completed successfully
operationFailed: false,       // Whether operation failed
operationWs: null,           // WebSocket connection
operationModalInstance: null  // Bootstrap modal instance
```

### Key Methods

- `openOperationModal(action)`: Opens modal and initializes for given action
- `connectOperationWebSocket()`: Establishes WebSocket connection
- `addOperationMessage(type, message)`: Adds message to display with auto-scroll
- `closeOperationModal()`: Closes modal and cleans up resources

## User Experience Flow

1. User clicks any action button (e.g., "Start Server")
2. Modal immediately opens showing operation name
3. WebSocket connection is established
4. Real-time output appears in terminal area as operation executes
5. On completion/failure:
   - Operation status indicator updates (green checkmark or red X)
   - Close button becomes enabled
   - User can review full operation log before closing

## Benefits

- **No tab switching required**: Users can see operation progress immediately
- **Better visibility**: Terminal-style output is easier to read than scattered logs
- **Prevents confusion**: Clear indication when operation is complete
- **Improved UX**: Users don't need to navigate to Console & Logs tab
- **Error visibility**: Failures are immediately apparent with clear messaging

## Localization

All UI text supports internationalization through the existing i18n system:

### English (en-US)
- Operation Status
- Operation in progress...
- Operation completed
- Operation failed

### Chinese (zh-CN)
- 操作状态
- 操作进行中...
- 操作已完成
- 操作失败
