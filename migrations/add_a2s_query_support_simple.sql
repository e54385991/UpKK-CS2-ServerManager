-- Migration: Add A2S Query Support (Simple Version)
-- Description: Adds fields for A2S query configuration and monitoring
-- Date: 2024-11-18
-- Note: This is a simpler version without existence checks
-- Run each statement individually if any fails (column already exists)

-- Add A2S query host field
ALTER TABLE servers 
ADD COLUMN a2s_query_host VARCHAR(255) NULL 
COMMENT 'Custom A2S query host (defaults to server host if not set)';

-- Add A2S query port field
ALTER TABLE servers 
ADD COLUMN a2s_query_port INT NULL 
COMMENT 'Custom A2S query port (defaults to game port if not set)';

-- Add enable A2S monitoring field
ALTER TABLE servers 
ADD COLUMN enable_a2s_monitoring TINYINT(1) DEFAULT 0 
COMMENT 'Enable A2S query monitoring for auto-restart';

-- Add A2S failure threshold field
ALTER TABLE servers 
ADD COLUMN a2s_failure_threshold INT DEFAULT 3 
COMMENT 'Number of consecutive A2S failures before triggering restart (1-10)';

-- Add A2S check interval field
ALTER TABLE servers 
ADD COLUMN a2s_check_interval_seconds INT DEFAULT 60
COMMENT 'A2S check interval in seconds (minimum: 15)';

-- Verify the columns were added
SELECT 
    COLUMN_NAME, 
    DATA_TYPE, 
    COLUMN_DEFAULT, 
    COLUMN_COMMENT 
FROM 
    INFORMATION_SCHEMA.COLUMNS 
WHERE 
    TABLE_SCHEMA = DATABASE() 
    AND TABLE_NAME = 'servers' 
    AND COLUMN_NAME IN ('a2s_query_host', 'a2s_query_port', 'enable_a2s_monitoring', 'a2s_failure_threshold', 'a2s_check_interval_seconds')
ORDER BY 
    COLUMN_NAME;
