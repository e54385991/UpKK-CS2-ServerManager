-- Migration: Add auto-clear crash history configuration fields
-- Date: 2025-11-18
-- Description: Adds auto_clear_crash_hours and last_status_check columns to servers table
-- This allows configurable auto-cleanup of crash_history.log based on offline duration

-- Add auto_clear_crash_hours column (hours offline before auto-clearing crash history)
-- NULL or 0 means disabled, otherwise specifies the threshold in hours
ALTER TABLE servers 
ADD COLUMN auto_clear_crash_hours INT DEFAULT NULL 
COMMENT 'Hours offline before auto-clearing crash history (0 or NULL = disabled, recommended: 2)';

-- Add last_status_check column (tracks when status was last checked)
-- This is updated every time the server status is checked
ALTER TABLE servers 
ADD COLUMN last_status_check DATETIME DEFAULT NULL
COMMENT 'Last time server status was checked';

-- Create index on last_status_check for efficient queries
CREATE INDEX idx_servers_last_status_check ON servers(last_status_check);
