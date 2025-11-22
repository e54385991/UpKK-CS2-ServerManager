# Testing Guide for Operation Status Modal

## Overview
This guide explains how to test the new Operation Status Modal feature that provides real-time feedback for server operations.

## Prerequisites
- Running CS2 Server Manager instance
- At least one CS2 server configured in the system
- Access to the server detail page

## Test Scenarios

### 1. Single Operation Tests

#### Test 1.1: Deploy Server
**Steps:**
1. Navigate to server detail page
2. Go to "Actions" tab
3. Click "Deploy Server" button
4. **Expected Results:**
   - Modal opens immediately with title "Operation Status: Deploy Server"
   - Terminal area shows "Connected to operation monitor"
   - Real-time output appears as deployment progresses
   - Messages are color-coded (blue for status, white for output)
   - Auto-scrolls to show latest messages
   - Close button is disabled during operation
   - On completion, shows green checkmark with "Operation completed"
   - Close button becomes enabled

#### Test 1.2: Start Server
**Steps:**
1. From server detail page, click "Start" button
2. **Expected Results:**
   - Modal opens with title "Operation Status: Start"
   - Shows real-time start progress
   - Completes with success or error message

#### Test 1.3: Stop Server
**Steps:**
1. Click "Stop" button
2. **Expected Results:**
   - Modal shows stop operation progress
   - Displays server shutdown messages
   - Updates server status on completion

#### Test 1.4: Restart Server
**Steps:**
1. Click "Restart" button
2. **Expected Results:**
   - Modal shows both stop and start sequences
   - Displays crash history cleanup (if applicable)
   - Shows complete restart process

#### Test 1.5: Update Server
**Steps:**
1. Click "Update" button
2. **Expected Results:**
   - Modal shows SteamCMD update process
   - Displays download progress
   - Shows validation if applicable

#### Test 1.6: Update + Validate Server
**Steps:**
1. Click "Update + Validate" button
2. **Expected Results:**
   - Similar to update but with full file validation
   - Shows verification progress

### 2. Plugin Installation Tests

#### Test 2.1: Install Single Plugin
**Steps:**
1. Scroll to "Plugin Framework Management" section
2. Click "Install" under any plugin (e.g., Metamod:Source)
3. **Expected Results:**
   - Modal opens showing installation progress
   - Displays download and extraction steps
   - Shows success or failure clearly

#### Test 2.2: Batch Install Multiple Plugins
**Steps:**
1. In "Batch Installation" section, check multiple plugins:
   - ☑ Metamod:Source
   - ☑ CounterStrikeSharp
   - ☑ CS2Fixes
2. Click "Install Selected Frameworks"
3. **Expected Results:**
   - Modal opens and stays open for all installations
   - Shows separator between each plugin: "--- Starting: Install Metamod:Source ---"
   - Displays progress for each plugin sequentially
   - Properly formatted plugin list in initial log message

#### Test 2.3: Update Plugin
**Steps:**
1. Click "Update" button under any installed plugin
2. **Expected Results:**
   - Modal shows update process
   - Displays version comparison if available

### 3. Error Handling Tests

#### Test 3.1: Network Error
**Steps:**
1. Start an operation
2. Disconnect network or stop backend service mid-operation
3. **Expected Results:**
   - Modal displays "WebSocket connection error"
   - Shows red error indicator
   - Close button becomes enabled

#### Test 3.2: SSH Connection Failure
**Steps:**
1. Configure server with invalid SSH credentials
2. Try to start the server
3. **Expected Results:**
   - Modal shows connection error
   - Displays detailed error message
   - Operation status shows as "failed" with red X

#### Test 3.3: Operation Conflict
**Steps:**
1. Start a long-running operation (e.g., Deploy)
2. Try to start another operation on the same server
3. **Expected Results:**
   - Backend returns 409 Conflict
   - Error message displayed in modal or alert
   - User informed that operation is already in progress

### 4. UI/UX Tests

#### Test 4.1: Modal Cannot Be Dismissed During Operation
**Steps:**
1. Start any operation
2. Try to close modal by:
   - Clicking close button (should be disabled)
   - Clicking backdrop (should not close - static backdrop)
   - Pressing Escape key (should not close)
3. **Expected Results:**
   - Modal stays open until operation completes
   - Close button is disabled
   - Backdrop and keyboard don't dismiss modal

#### Test 4.2: Modal Can Be Closed After Completion
**Steps:**
1. Wait for operation to complete (success or failure)
2. Click close button
3. **Expected Results:**
   - Modal closes cleanly
   - WebSocket connection is terminated
   - Modal state is reset for next operation

#### Test 4.3: Auto-scroll Functionality
**Steps:**
1. Start operation that produces many log lines (e.g., Deploy)
2. Manually scroll up in terminal area
3. **Expected Results:**
   - New messages appear at bottom
   - Auto-scroll brings view to bottom when new message arrives
   - User can read previous messages by scrolling up

#### Test 4.4: Color Coding
**Steps:**
1. Observe different message types during operations
2. **Expected Results:**
   - Info messages: white text
   - Status messages: blue text
   - Success/Complete: green text
   - Errors: red text
   - Warnings: yellow text

### 5. Internationalization Tests

#### Test 5.1: English Language
**Steps:**
1. Set browser/app language to English
2. Perform any operation
3. **Expected Results:**
   - Modal title: "Operation Status"
   - Progress indicator: "Operation in progress..."
   - Success: "Operation completed"
   - Failure: "Operation failed"
   - Batch install: "Installing metamod and counterstrikesharp..."

#### Test 5.2: Chinese Language
**Steps:**
1. Set language to Chinese (zh-CN)
2. Perform any operation
3. **Expected Results:**
   - Modal title: "操作状态"
   - Progress indicator: "操作进行中..."
   - Success: "操作已完成"
   - Failure: "操作失败"
   - Batch install: "Installing metamod和counterstrikesharp..."

### 6. Edge Cases

#### Test 6.1: Rapid Multiple Operations
**Steps:**
1. Click Deploy button
2. Quickly click other action buttons
3. **Expected Results:**
   - Only first operation executes
   - Subsequent clicks are ignored (isActionRunning guard)
   - No duplicate modals appear

#### Test 6.2: Modal Element Missing
**Steps:**
1. Use browser dev tools to remove modal element from DOM
2. Try to execute an operation
3. **Expected Results:**
   - Error logged to console
   - Graceful fallback (error message in main logs)
   - Application doesn't crash

#### Test 6.3: Malformed WebSocket Data
**Steps:**
1. If possible, inject malformed JSON through WebSocket
2. **Expected Results:**
   - Error is caught and logged
   - Modal displays "Invalid message received from server"
   - Application continues functioning

## Manual Verification Checklist

- [ ] Modal opens for all operation types
- [ ] Real-time output displays correctly
- [ ] Color coding works for all message types
- [ ] Auto-scroll functions properly
- [ ] Close button disabled/enabled correctly
- [ ] Batch operations work with modal reuse
- [ ] English translations display correctly
- [ ] Chinese translations display correctly
- [ ] Error handling works as expected
- [ ] WebSocket connection cleanup happens on close
- [ ] No memory leaks (modal state resets properly)
- [ ] No console errors during normal operation
- [ ] Works across different browsers (Chrome, Firefox, Safari, Edge)
- [ ] Responsive on mobile devices

## Screenshots to Take

For PR documentation, capture screenshots showing:
1. Modal open during a deployment operation
2. Modal showing real-time output
3. Modal displaying success state (green checkmark)
4. Modal displaying error state (red X)
5. Batch plugin installation with separator messages
6. English version
7. Chinese version

## Performance Considerations

- Monitor WebSocket connection count (should be 1 per modal)
- Check memory usage during long operations
- Verify cleanup happens on modal close
- Ensure no WebSocket connection leaks

## Known Limitations

1. Modal reuses WebSocket for batch operations (by design)
2. Cannot dismiss modal during operation (by design, for safety)
3. Messages limited to last shown (no pagination, but memory efficient)

## Reporting Issues

If you find any issues during testing, report:
- Operation type being performed
- Browser and version
- Language setting
- Steps to reproduce
- Expected vs actual behavior
- Console errors (if any)
- Screenshots or video if possible
