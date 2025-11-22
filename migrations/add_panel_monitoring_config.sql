-- Migration: Add panel-based monitoring configuration
-- Date: 2025-11-18
-- Description: Adds web panel monitoring configuration independent of local autorestart
-- This allows monitoring and auto-restart from the web backend

-- Add enable_panel_monitoring column (enable/disable panel-based monitoring)
ALTER TABLE servers 
ADD COLUMN enable_panel_monitoring BOOLEAN DEFAULT FALSE 
COMMENT 'Enable web panel monitoring and auto-restart (independent of local autorestart)';

-- Add monitor_interval_seconds column (how often to check server status)
ALTER TABLE servers 
ADD COLUMN monitor_interval_seconds INT DEFAULT 60 
COMMENT 'How often to check server status in seconds (10-3600, default: 60)';

-- Add auto_restart_on_crash column (whether to auto-restart when process not found)
ALTER TABLE servers 
ADD COLUMN auto_restart_on_crash BOOLEAN DEFAULT TRUE
COMMENT 'Auto-restart if process not found (when panel monitoring enabled)';

-- Create index on enable_panel_monitoring for efficient queries
CREATE INDEX idx_servers_panel_monitoring ON servers(enable_panel_monitoring);
