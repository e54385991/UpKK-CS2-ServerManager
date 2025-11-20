# WebSSH Console Usage Guide

## Quick Start

### Opening Consoles

#### Method 1: Direct URLs
```
# SSH Console - Full interactive terminal
http://your-server:8000/servers/1/ssh-console

# Game Console - Read-only server output
http://your-server:8000/servers/1/game-console

# Popup (Backward Compatible)
http://your-server:8000/servers/1/console-popup/ssh
http://your-server:8000/servers/1/console-popup/game
```

#### Method 2: JavaScript (Popup Window)
```javascript
// SSH Console
function openSSHConsole(serverId) {
    window.open(
        `/servers/${serverId}/ssh-console`,
        `ssh_console_${serverId}`,
        'width=1200,height=700,menubar=no,toolbar=no,location=no'
    );
}

// Game Console
function openGameConsole(serverId) {
    window.open(
        `/servers/${serverId}/game-console`,
        `game_console_${serverId}`,
        'width=1200,height=700,menubar=no,toolbar=no,location=no'
    );
}
```

#### Method 3: Embedded in Page
```html
<!-- SSH Console iframe -->
<iframe 
    src="/servers/1/ssh-console" 
    width="100%" 
    height="600px" 
    frameborder="0">
</iframe>

<!-- Game Console iframe -->
<iframe 
    src="/servers/1/game-console" 
    width="100%" 
    height="600px" 
    frameborder="0">
</iframe>
```

## Features

### SSH Console Features

#### Interactive Shell
- Full bash/zsh/sh command execution
- Tab completion support
- Command history (arrow keys)
- Multi-line editing
- Ctrl+C interrupt support

#### Terminal Controls
- **Ctrl+Shift+C**: Copy selected text
- **Ctrl+Shift+V**: Paste from clipboard
- **Ctrl+Shift+F**: Find in terminal (built-in)
- **Ctrl+L**: Clear screen
- **Arrow Keys**: Command history navigation

#### Visual Features
- ✅ Status bar with connection indicator
- ✅ Loading overlay during initialization
- ✅ Auto-reconnection with countdown
- ✅ Professional VS Code theme
- ✅ Clickable URLs (Ctrl+Click)
- ✅ Smooth scrolling
- ✅ Auto-resize on window change

### Game Console Features

#### Read-Only Monitor
- Real-time server output
- Automatic scrolling to latest output
- 20,000 line scrollback buffer
- No input required (monitoring only)

#### Visual Features
- ✅ Gaming-themed color scheme
- ✅ Server ID display in status bar
- ✅ Connection status indicator
- ✅ Loading state
- ✅ Auto-reconnection

## Terminal Customization

### Changing Font Size

Edit the template and modify the `fontSize` property:

```javascript
// ssh_console.html or game_console.html
term = new Terminal({
    fontSize: 16,  // Change from 14 to 16
    // ... other options
});
```

### Changing Theme Colors

Modify the `theme` object in the template:

```javascript
theme: {
    background: '#1e1e1e',    // Dark background
    foreground: '#d4d4d4',    // Light text
    cursor: '#aeafad',        // Cursor color
    // ... more colors
}
```

### Available Themes

#### Dark Themes
- **VS Code Dark** (default for SSH)
- **One Dark**
- **Dracula**
- **Nord**

#### Light Themes
- **VS Code Light**
- **Solarized Light**
- **GitHub Light**

To apply a different theme, just replace the theme object values.

### Changing Font Family

```javascript
term = new Terminal({
    fontFamily: "'Fira Code', 'Cascadia Code', monospace",
    // ... other options
});
```

Popular monospace fonts:
- **Cascadia Code** (default, includes ligatures)
- **Fira Code** (ligatures, developer-friendly)
- **JetBrains Mono**
- **Source Code Pro**
- **Consolas**
- **Monaco**

## Advanced Usage

### Adding More Addons

xterm.js supports many addons. To add search:

1. Download addon:
```bash
curl -L https://cdn.jsdelivr.net/npm/xterm-addon-search@0.13.0/lib/xterm-addon-search.js \
  -o static/xterm/xterm-addon-search.js
```

2. Add to template:
```html
<script src="/static/xterm/xterm-addon-search.js"></script>
```

3. Initialize in JavaScript:
```javascript
const searchAddon = new SearchAddon.SearchAddon();
term.loadAddon(searchAddon);

// Usage: Search for text
searchAddon.findNext('error');
```

### Available Addons
- **xterm-addon-search**: Find in terminal
- **xterm-addon-unicode11**: Better Unicode support
- **xterm-addon-serialize**: Serialize terminal state
- **xterm-addon-attach**: Attach to WebSocket directly
- **xterm-addon-image**: Display images in terminal

### Terminal Size Management

The terminal automatically resizes, but you can control it:

```javascript
// Get current size
console.log(`Terminal size: ${term.cols} x ${term.rows}`);

// Manually resize
term.resize(120, 30);  // cols, rows

// Listen for resize events
term.onResize(({ cols, rows }) => {
    console.log(`Resized to ${cols}x${rows}`);
});
```

### Scrollback Buffer

Control how many lines are kept in history:

```javascript
term = new Terminal({
    scrollback: 20000,  // Keep 20,000 lines
    // ... other options
});
```

Memory usage:
- 1,000 lines ≈ 50 KB
- 10,000 lines ≈ 500 KB
- 20,000 lines ≈ 1 MB

## Troubleshooting

### Console Not Connecting

**Symptoms**: "Connecting..." status never changes

**Solutions**:
1. Check WebSocket endpoint is accessible
2. Verify server firewall allows WebSocket connections
3. Check browser console for errors
4. Ensure server is configured in database

**Debug**:
```javascript
// Open browser console (F12) and check for:
WebSocket connection to 'ws://...' failed: Error
```

### Terminal Not Displaying

**Symptoms**: Blank screen, no terminal visible

**Solutions**:
1. Check xterm.js files are loaded (F12 → Network tab)
2. Verify terminal container has height
3. Check JavaScript console for errors

**Debug**:
```javascript
// Check if Terminal is defined
console.log(typeof Terminal); // Should be 'function'

// Check terminal instance
console.log(term); // Should show Terminal object
```

### Input Not Working

**Symptoms**: Can see output but can't type

**Solutions**:
1. Click on terminal to focus
2. Check WebSocket is connected
3. Verify input handler is registered

**Debug**:
```javascript
// Check WebSocket state
console.log(ws.readyState); // Should be 1 (OPEN)

// Check if onData is registered
console.log(term._core._inputHandler); // Should exist
```

### Slow Performance

**Symptoms**: Lag when scrolling or typing

**Solutions**:
1. Reduce scrollback buffer size
2. Clear terminal output (Ctrl+L)
3. Check CPU usage (might be server-side issue)

**Optimize**:
```javascript
// Reduce scrollback
scrollback: 5000,  // Instead of 20000

// Disable WebGL if causing issues
rendererType: 'dom',  // or 'canvas'
```

### Characters Look Wrong

**Symptoms**: Box characters, missing symbols

**Solutions**:
1. Install proper fonts (Cascadia Code, Fira Code)
2. Enable Unicode addon
3. Check font-family setting

**Fix**:
```javascript
// Ensure good font fallbacks
fontFamily: "'Cascadia Code', 'Consolas', 'Courier New', monospace"
```

## Integration Examples

### React Component
```jsx
import React, { useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';

function SSHConsole({ serverId }) {
    const terminalRef = useRef(null);
    const term = useRef(null);
    
    useEffect(() => {
        term.current = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            theme: { background: '#1e1e1e' }
        });
        
        const fitAddon = new FitAddon();
        term.current.loadAddon(fitAddon);
        
        term.current.open(terminalRef.current);
        fitAddon.fit();
        
        // Connect WebSocket
        const ws = new WebSocket(
            `ws://localhost:8000/servers/${serverId}/ssh-console`
        );
        
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'output') {
                term.current.write(msg.data);
            }
        };
        
        term.current.onData((data) => {
            ws.send(JSON.stringify({ type: 'input', data }));
        });
        
        return () => {
            term.current.dispose();
            ws.close();
        };
    }, [serverId]);
    
    return <div ref={terminalRef} style={{ height: '600px' }} />;
}
```

### Vue Component
```vue
<template>
    <div ref="terminal" class="terminal-container"></div>
</template>

<script>
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';

export default {
    props: ['serverId'],
    
    mounted() {
        this.term = new Terminal({
            cursorBlink: true,
            fontSize: 14
        });
        
        this.fitAddon = new FitAddon();
        this.term.loadAddon(this.fitAddon);
        
        this.term.open(this.$refs.terminal);
        this.fitAddon.fit();
        
        this.connectWebSocket();
    },
    
    methods: {
        connectWebSocket() {
            this.ws = new WebSocket(
                `ws://localhost:8000/servers/${this.serverId}/ssh-console`
            );
            
            this.ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                if (msg.type === 'output') {
                    this.term.write(msg.data);
                }
            };
            
            this.term.onData((data) => {
                this.ws.send(JSON.stringify({ type: 'input', data }));
            });
        }
    },
    
    beforeUnmount() {
        this.term.dispose();
        this.ws.close();
    }
}
</script>

<style>
.terminal-container {
    height: 600px;
}
</style>
```

## Best Practices

### Performance
1. ✅ Use reasonable scrollback limits (10K-20K)
2. ✅ Enable FitAddon for auto-sizing
3. ✅ Dispose terminal on unmount/close
4. ✅ Reuse WebSocket connections when possible

### Security
1. ✅ Always use WSS (WebSocket Secure) in production
2. ✅ Validate server authentication
3. ✅ Sanitize user input before sending
4. ✅ Implement rate limiting on WebSocket

### User Experience
1. ✅ Show connection status clearly
2. ✅ Provide loading indicators
3. ✅ Implement auto-reconnection
4. ✅ Handle errors gracefully
5. ✅ Allow users to resize terminal

### Code Organization
1. ✅ Keep templates independent
2. ✅ Use separate themes for different purposes
3. ✅ Document WebSocket message format
4. ✅ Add comments for customization points

## Resources

### Official Documentation
- [xterm.js Docs](https://xtermjs.org/)
- [xterm.js API](https://github.com/xtermjs/xterm.js/blob/master/typings/xterm.d.ts)
- [Addons](https://github.com/xtermjs/xterm.js/tree/master/addons)

### Community
- [GitHub Discussions](https://github.com/xtermjs/xterm.js/discussions)
- [Stack Overflow](https://stackoverflow.com/questions/tagged/xtermjs)

### Examples
- [VS Code Terminal](https://github.com/microsoft/vscode)
- [Hyper Terminal](https://github.com/vercel/hyper)
- [Terminus](https://github.com/Eugeny/terminus)

## Support

For issues with the CS2 Server Manager consoles:
1. Check this documentation
2. Review browser console for errors
3. Validate templates with validation script
4. Check server logs
5. Open GitHub issue if problem persists
