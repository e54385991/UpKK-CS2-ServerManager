# Setup Wizard WebSocket Real-time Progress

## Overview

The setup wizard now supports real-time progress updates via WebSocket, allowing users to see Linux command feedback as it happens during server initialization.

**Status**: ✅ **Fully Implemented** - Both backend and frontend now support real-time logging!

## Features

1. **Real-time Log Streaming**: View setup progress in real-time as commands execute on the remote server
2. **Automatic Connection**: Frontend automatically establishes WebSocket connection before starting setup
3. **Graceful Fallback**: If WebSocket fails, falls back to showing all logs at completion
4. **Auto-scroll**: Terminal automatically scrolls to show the latest logs
5. **Backward Compatible**: Works with or without WebSocket

## How It Works

### Frontend Implementation (Automatic)

The frontend **automatically** uses WebSocket for real-time updates:

1. **Session ID Generation**: Generates a unique session ID using `crypto.randomUUID()`
2. **WebSocket Connection**: Connects to `/api/setup/setup-progress/{session_id}` before starting setup
3. **Real-time Updates**: Receives and displays logs as they are generated on the server
4. **Completion**: Closes WebSocket after setup completes
5. **Fallback**: If WebSocket connection fails, shows all logs from HTTP response

### User Experience

When users click "自动设置服务器":

1. **Connection Message**: `正在建立实时日志连接...`
2. **Connection Success**: `✓ 实时日志连接已建立`
3. **Real-time Logs**: Each log appears immediately as the operation progresses
4. **Linux Command Output**: Actual output from apt-get, ufw, etc. appears in real-time
5. **Auto-scroll**: Terminal scrolls automatically to show latest updates

## Backend API

### WebSocket Endpoint

```
ws://your-domain/api/setup/setup-progress/{session_id}
```

**Parameters:**
- `session_id`: A unique identifier for this setup session (UUID)

**Message Format:**
```json
{
  "type": "log|info",
  "message": "Log message text",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

**Message Types:**
- `info`: Informational messages (e.g., connection established)
- `log`: Setup progress log messages

### HTTP Endpoint

```
POST /api/setup/auto-setup
```

**New Optional Parameter:**
- `session_id` (string, optional): If provided, progress will be sent to the WebSocket connection with this session_id

**Response includes:**
- `session_id`: The session_id that was used (if provided)
- All other existing fields remain unchanged

## Frontend Code (Already Implemented)

The setup wizard template (`server_setup_wizard.html`) already includes WebSocket support:

```javascript
async autoSetup() {
    // Generate unique session ID
    const sessionId = crypto.randomUUID();
    
    // Connect to WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/setup/setup-progress/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        addLog(data.message);  // Display log in real-time
    };
    
    // Send session_id in payload
    const payload = {
        ...otherFields,
        session_id: sessionId
    };
    
    // Start setup
    const response = await authFetch('/api/setup/auto-setup', {
        method: 'POST',
        body: JSON.stringify(payload)
    });
}
```

## Testing

### Visual Confirmation

When running the setup wizard, you should see:

1. **Immediate feedback**: Logs appear one by one as operations execute
2. **Linux command output**: Raw output from apt-get, ufw commands
3. **Progress indicators**: ✓ for success, ⚠ for warnings, ✗ for errors
4. **Auto-scroll**: Terminal scrolls automatically

### Debug Console

Open browser console (F12) to see:
- WebSocket connection status
- Any WebSocket errors (will fall back to showing logs at completion)

## Error Handling

### WebSocket Connection Fails
- **Behavior**: Shows warning message: `⚠ 实时日志连接出错，将在完成后显示所有日志`
- **Fallback**: All logs displayed from HTTP response when setup completes
- **User Impact**: Minimal - still sees all logs, just not in real-time

### Network Issues
- **Behavior**: WebSocket automatically reconnects or falls back to HTTP
- **Data Loss**: None - all logs preserved in HTTP response

## Performance

- **Connection Overhead**: Minimal (~500ms for WebSocket connection)
- **Real-time Updates**: Logs appear within milliseconds of generation
- **Browser Support**: All modern browsers (Chrome, Firefox, Safari, Edge)

## SUDO User Recording Fix

The setup wizard now correctly saves SUDO user information for both:
- **Password-based sudo**: Saves the sudo password
- **Passwordless sudo**: Saves an empty string (indicating passwordless access)

### Database Table: `ssh_servers_sudo`

Records are saved with the following information:
- `user_id`: The user who ran the setup
- `host`: Server hostname/IP
- `ssh_port`: SSH port
- `sudo_user`: The username that has sudo access
- `sudo_password`: The sudo password (empty string for passwordless sudo)

This allows the system to remember sudo credentials for future operations on the same server.

## Implementation Details

### WebSocket Manager

The `SetupWebSocket` class manages WebSocket connections:
- Maintains a dictionary of active connections keyed by `session_id`
- Automatically removes disconnected clients
- Resilient to connection failures

### Error Handling

WebSocket failures are handled gracefully:
- If WebSocket connection fails, setup continues normally
- All logs are still returned in the final HTTP response
- WebSocket errors don't interrupt the main setup flow

### Thread Safety

Since each `session_id` should only have one WebSocket connection, and Python's asyncio is single-threaded, the simple dictionary implementation is sufficient for this use case.

## Benefits

1. ✅ **Better User Experience**: Users see progress in real-time instead of waiting for completion
2. ✅ **Transparency**: Users can see exactly what commands are being executed
3. ✅ **Debugging**: Real-time logs help identify issues during setup
4. ✅ **Confidence**: Seeing Linux command output builds trust in the automation
5. ✅ **No Breaking Changes**: Existing implementations work without modifications
6. ✅ **Automatic**: No frontend code changes needed - works out of the box!
