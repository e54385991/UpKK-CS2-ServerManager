# CS2 Server Startup Guide

## Common Startup Issues and Solutions

CS2 dedicated servers often fail to start after deployment due to several common issues. This guide explains how we've addressed them following LinuxGSM best practices.

## LGSM-Inspired Startup Implementation

### Key Changes Based on LGSM

1. **Working Directory**
   - CS2 must be started from its bin directory: `game/bin/linuxsteamrt64/`
   - This allows CS2 to find its libraries and dependencies

2. **Library Path (LD_LIBRARY_PATH)**
   - Must include the bin directory
   - Ensures CS2 can load required shared libraries

3. **Executable Permissions**
   - The CS2 binary must have execute permissions (+x)
   - Automatically set before startup

4. **Output Redirection**
   - Console output is logged to `game/csgo/console.log`
   - Uses `tee` to capture output while server runs

5. **Environment Setup**
   - Proper bash environment with library paths
   - Screen session for detached operation

## Startup Command Structure

### Old (Problematic) Approach
```bash
screen -dmS cs2server_1 /path/to/cs2/game/bin/linuxsteamrt64/cs2 -dedicated ...
```

**Problems:**
- Wrong working directory
- Missing library paths
- No output capture
- Permissions not verified

### New (LGSM-Style) Approach
```bash
cd /path/to/cs2/game/bin/linuxsteamrt64 && \
export LD_LIBRARY_PATH="/path/to/cs2/game/bin/linuxsteamrt64:$LD_LIBRARY_PATH" && \
screen -dmS cs2server_1 \
  bash -c './cs2 -dedicated -port 27015 +map de_dust2 ... 2>&1 | tee /path/to/cs2/game/csgo/console.log'
```

**Benefits:**
- ✅ Correct working directory
- ✅ Library path properly set
- ✅ Output captured to log file
- ✅ Relative path for executable
- ✅ Proper bash environment

## Verification Methods

The startup process uses multiple verification methods:

### 1. Screen Session Check
```bash
screen -list | grep cs2server_1
```
Verifies the screen session is running.

### 2. Process Check
```bash
pgrep -f 'cs2.*-port 27015'
```
Verifies the CS2 process is actually running.

### 3. Port Listening Check (NEW)
```bash
netstat -tuln | grep ':27015' || ss -tuln | grep ':27015'
```
Verifies the server is listening on the configured port.

### 4. Log File Check
```bash
tail -30 /path/to/cs2/game/csgo/console.log
```
Provides diagnostic information if startup fails.

## Common Startup Failures

### 1. Missing Libraries

**Symptom:** Server starts but immediately exits

**Solution:** Install required 32-bit libraries:
```bash
sudo apt-get install lib32gcc-s1 lib32stdc++6
```

### 2. Port Already in Use

**Symptom:** Server fails to bind to port

**Log shows:** "WARNING: UDP_OpenSocket: port: 27015 bind: Cannot assign requested address"

**Solution:** 
- Change the `game_port` to an unused port
- Or kill the process using the port

### 3. Invalid Map

**Symptom:** Server starts but crashes

**Log shows:** "Map not found" or "Failed to load map"

**Solution:** Ensure `default_map` is a valid installed map (de_dust2, de_mirage, etc.)

### 4. Permissions Issue

**Symptom:** "Permission denied" error

**Solution:** The system now automatically runs `chmod +x` on the CS2 binary

### 5. Memory/Resource Issues

**Symptom:** Server starts but kills itself

**Log shows:** Memory allocation errors

**Solution:** Ensure the server has sufficient RAM (minimum 2GB recommended)

## Debugging Failed Startups

### 1. Check Console Log
```bash
tail -50 /path/to/cs2/game/csgo/console.log
```

### 2. Check Screen Session
```bash
screen -list
screen -r cs2server_1
```

### 3. Check Process Status
```bash
ps aux | grep cs2
```

### 4. Check Port Usage
```bash
netstat -tuln | grep 27015
```

### 5. Check System Resources
```bash
free -h
df -h
```

## Best Practices

### 1. Use Standard Ports
- Game port: 27015 (default)
- Client port: 27016 (game_port + 1)
- SourceTV port: 27020

### 2. Verify Deployment
Before starting, ensure:
- CS2 files are fully downloaded (~30GB)
- Executable has correct permissions
- Required libraries are installed
- Sufficient disk space available

### 3. Monitor First Start
Watch the console log during first startup:
```bash
tail -f /path/to/cs2/game/csgo/console.log
```

### 4. Use Valid Maps
Stick to official maps for initial testing:
- de_dust2 (most reliable)
- de_mirage
- de_inferno
- de_nuke

### 5. Start Simple
Begin with minimal configuration:
```json
{
  "server_name": "Test Server",
  "default_map": "de_dust2",
  "max_players": 10,
  "tickrate": 64
}
```

Add advanced features after confirming basic startup works.

## Startup Wait Times

- **Initial wait:** 5 seconds (increased from 3)
- **Verification retries:** 3 methods attempted
- **Total startup time:** Up to 10 seconds for full verification

CS2 can take longer to start than older Source games. The increased wait time prevents false negatives.

## Environment Variables

The following environment variables are set during startup:

### LD_LIBRARY_PATH
```bash
export LD_LIBRARY_PATH="/path/to/cs2/game/bin/linuxsteamrt64:$LD_LIBRARY_PATH"
```

This ensures CS2 can find:
- `libsteam_api.so`
- `libtier0_s64.so`
- Other required shared libraries

## Comparison with LinuxGSM

Our implementation follows LinuxGSM's proven approach:

| Feature | LGSM | Our Implementation |
|---------|------|-------------------|
| Working directory | ✅ bin dir | ✅ bin dir |
| LD_LIBRARY_PATH | ✅ Set | ✅ Set |
| Output logging | ✅ tee to file | ✅ tee to file |
| Permissions check | ✅ chmod +x | ✅ chmod +x |
| Screen session | ✅ detached | ✅ detached |
| Multiple verification | ✅ yes | ✅ yes (enhanced) |

## References

- [LinuxGSM CS2 Server](https://github.com/GameServerManagers/LinuxGSM/blob/master/lgsm/functions/command_start.sh)
- [CS2 Dedicated Server Wiki](https://developer.valvesoftware.com/wiki/Counter-Strike_2/Dedicated_Servers)
- [Source Dedicated Server](https://developer.valvesoftware.com/wiki/Source_Dedicated_Server)
