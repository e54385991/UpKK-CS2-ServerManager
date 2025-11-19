-- Add auto-update and version tracking fields to servers table
-- This migration adds support for:
-- 1. Current game version tracking
-- 2. Auto-update based on Steam API version check
-- 3. Update check and update time tracking
-- 4. Configurable update check interval

-- Add current_game_version column to track installed CS2 version
ALTER TABLE servers
ADD COLUMN current_game_version VARCHAR(50) NULL
COMMENT 'Current installed CS2 version from A2S query or manual entry';

-- Add enable_auto_update column (default: TRUE)
ALTER TABLE servers
ADD COLUMN enable_auto_update BOOLEAN DEFAULT TRUE
COMMENT 'Enable automatic updates based on Steam API version check';

-- Add update_check_interval_hours column (default: 1 hour)
ALTER TABLE servers
ADD COLUMN update_check_interval_hours INT DEFAULT 1
COMMENT 'Hours between version checks (1-24)';

-- Add last_update_check column to track when version was last checked
ALTER TABLE servers
ADD COLUMN last_update_check DATETIME NULL
COMMENT 'Last time version was checked against Steam API';

-- Add last_update_time column to track when server was last updated
ALTER TABLE servers
ADD COLUMN last_update_time DATETIME NULL
COMMENT 'Last time server was updated';

-- Add index on last_update_check for efficient querying of servers that need version check
CREATE INDEX idx_last_update_check ON servers(last_update_check);
