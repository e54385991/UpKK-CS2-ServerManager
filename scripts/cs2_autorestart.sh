#!/bin/bash
#
# CS2 Server Auto-Restart Wrapper Script
# This script wraps the CS2 server with automatic restart functionality
# and crash protection to prevent infinite restart loops.
#
# Usage: cs2_autorestart.sh <server_id> <api_key> <backend_url> <game_directory> <start_command>
#
# Arguments:
#   server_id: Server ID for status reporting
#   api_key: API key for authenticating with backend
#   backend_url: URL of the backend management system
#   game_directory: Base game directory
#   start_command: Full command to start the CS2 server
#

# Parse arguments
SERVER_ID="$1"
API_KEY="$2"
BACKEND_URL="$3"
GAME_DIR="$4"
shift 4
START_COMMAND="$@"

# Configuration
MAX_RESTARTS=5          # Maximum number of restarts within time window
TIME_WINDOW=600         # Time window in seconds (10 minutes)
RESTART_DELAY=10        # Delay before restart in seconds
CRASH_LOG="${GAME_DIR}/crash_history.log"

# Initialize crash history file if it doesn't exist
if [ ! -f "$CRASH_LOG" ]; then
    touch "$CRASH_LOG"
fi

# Function to report status to backend
report_status() {
    local event_type="$1"
    local message="$2"
    local exit_code="$3"
    local restart_count="$4"
    
    # Build JSON payload
    local json_data=$(cat <<EOF
{
    "event_type": "$event_type",
    "message": "$message",
    "exit_code": $exit_code,
    "restart_count": $restart_count
}
EOF
)
    
    # Send POST request to backend (suppress output, run in background)
    curl -X POST \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $API_KEY" \
        -d "$json_data" \
        "${BACKEND_URL}/api/server-status/${SERVER_ID}/report" \
        --silent --max-time 5 &
}

# Function to clean old crash records
clean_old_crashes() {
    local cutoff_time=$(($(date +%s) - TIME_WINDOW))
    
    # Create temporary file with only recent crashes
    local temp_file="${CRASH_LOG}.tmp"
    if [ -f "$CRASH_LOG" ]; then
        while IFS= read -r line; do
            local crash_time=$(echo "$line" | cut -d' ' -f1)
            if [ "$crash_time" -ge "$cutoff_time" ]; then
                echo "$line" >> "$temp_file"
            fi
        done < "$CRASH_LOG"
        
        # Replace crash log with cleaned version
        if [ -f "$temp_file" ]; then
            mv "$temp_file" "$CRASH_LOG"
        fi
    fi
}

# Function to record a crash
record_crash() {
    local timestamp=$(date +%s)
    echo "$timestamp $(date '+%Y-%m-%d %H:%M:%S')" >> "$CRASH_LOG"
}

# Function to count recent crashes
count_recent_crashes() {
    clean_old_crashes
    
    if [ -f "$CRASH_LOG" ]; then
        wc -l < "$CRASH_LOG" | tr -d ' '
    else
        echo "0"
    fi
}

# Function to check if restart is allowed
can_restart() {
    local crash_count=$(count_recent_crashes)
    
    if [ "$crash_count" -ge "$MAX_RESTARTS" ]; then
        return 1  # Cannot restart
    else
        return 0  # Can restart
    fi
}

# Report startup
report_status "startup" "Server starting with auto-restart protection" 0 0

echo "======================================================================"
echo "CS2 Server Auto-Restart Wrapper"
echo "======================================================================"
echo "Server ID: $SERVER_ID"
echo "Max Restarts: $MAX_RESTARTS in $TIME_WINDOW seconds"
echo "Restart Delay: $RESTART_DELAY seconds"
echo "Start Command: $START_COMMAND"
echo "======================================================================"
echo ""

# Main restart loop using 'until'
restart_count=0

until false; do
    # Check if restart is allowed
    if ! can_restart; then
        crash_count=$(count_recent_crashes)
        echo ""
        echo "======================================================================"
        echo "CRASH LIMIT REACHED"
        echo "======================================================================"
        echo "Server has crashed $crash_count times in the last $TIME_WINDOW seconds."
        echo "Auto-restart has been disabled to prevent restart loop."
        echo "Manual intervention required."
        echo "======================================================================"
        
        # Report crash limit reached to backend
        report_status "crash_limit_reached" "Server crashed $crash_count times, auto-restart disabled" 1 "$crash_count"
        
        # Exit the script
        exit 1
    fi
    
    # Start the server
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting CS2 server..."
    
    # Execute the start command
    eval $START_COMMAND
    EXIT_CODE=$?
    
    # Record the crash
    record_crash
    restart_count=$((restart_count + 1))
    crash_count=$(count_recent_crashes)
    
    echo ""
    echo "======================================================================"
    echo "SERVER EXITED"
    echo "======================================================================"
    echo "Exit Code: $EXIT_CODE"
    echo "Restart Count (total): $restart_count"
    echo "Crashes in last ${TIME_WINDOW}s: $crash_count"
    echo "======================================================================"
    
    # Report crash to backend
    report_status "crash" "Server crashed with exit code $EXIT_CODE" "$EXIT_CODE" "$crash_count"
    
    # Check if we can restart
    if ! can_restart; then
        # Will be caught in next loop iteration
        continue
    fi
    
    echo ""
    echo "Waiting $RESTART_DELAY seconds before restart..."
    sleep $RESTART_DELAY
    
    # Report restart to backend
    report_status "restart" "Restarting server after crash (attempt $restart_count)" "$EXIT_CODE" "$crash_count"
    
    echo ""
    echo "======================================================================"
    echo "RESTARTING SERVER"
    echo "======================================================================"
    echo ""
done
