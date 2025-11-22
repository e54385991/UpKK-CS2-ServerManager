-- Migration: Add A2S Query Support
-- Description: Adds fields for A2S query configuration and monitoring
-- Date: 2024-11-18
-- Note: Run this manually if automatic migration doesn't work

-- Check and add A2S query host field
SET @col_exists = (SELECT COUNT(*) 
                   FROM INFORMATION_SCHEMA.COLUMNS 
                   WHERE TABLE_SCHEMA = DATABASE() 
                   AND TABLE_NAME = 'servers' 
                   AND COLUMN_NAME = 'a2s_query_host');

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE servers ADD COLUMN a2s_query_host VARCHAR(255) NULL COMMENT ''Custom A2S query host (defaults to server host if not set)''',
    'SELECT ''Column a2s_query_host already exists'' AS status');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Check and add A2S query port field
SET @col_exists = (SELECT COUNT(*) 
                   FROM INFORMATION_SCHEMA.COLUMNS 
                   WHERE TABLE_SCHEMA = DATABASE() 
                   AND TABLE_NAME = 'servers' 
                   AND COLUMN_NAME = 'a2s_query_port');

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE servers ADD COLUMN a2s_query_port INT NULL COMMENT ''Custom A2S query port (defaults to game port if not set)''',
    'SELECT ''Column a2s_query_port already exists'' AS status');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Check and add enable A2S monitoring field
SET @col_exists = (SELECT COUNT(*) 
                   FROM INFORMATION_SCHEMA.COLUMNS 
                   WHERE TABLE_SCHEMA = DATABASE() 
                   AND TABLE_NAME = 'servers' 
                   AND COLUMN_NAME = 'enable_a2s_monitoring');

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE servers ADD COLUMN enable_a2s_monitoring TINYINT(1) DEFAULT 0 COMMENT ''Enable A2S query monitoring for auto-restart''',
    'SELECT ''Column enable_a2s_monitoring already exists'' AS status');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Check and add A2S failure threshold field
SET @col_exists = (SELECT COUNT(*) 
                   FROM INFORMATION_SCHEMA.COLUMNS 
                   WHERE TABLE_SCHEMA = DATABASE() 
                   AND TABLE_NAME = 'servers' 
                   AND COLUMN_NAME = 'a2s_failure_threshold');

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE servers ADD COLUMN a2s_failure_threshold INT DEFAULT 3 COMMENT ''Number of consecutive A2S failures before triggering restart (1-10)''',
    'SELECT ''Column a2s_failure_threshold already exists'' AS status');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Check and add A2S check interval field
SET @col_exists = (SELECT COUNT(*) 
                   FROM INFORMATION_SCHEMA.COLUMNS 
                   WHERE TABLE_SCHEMA = DATABASE() 
                   AND TABLE_NAME = 'servers' 
                   AND COLUMN_NAME = 'a2s_check_interval_seconds');

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE servers ADD COLUMN a2s_check_interval_seconds INT DEFAULT 60 COMMENT ''A2S check interval in seconds (minimum: 15)''',
    'SELECT ''Column a2s_check_interval_seconds already exists'' AS status');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Show success message
SELECT 'A2S query support migration completed!' AS status;
