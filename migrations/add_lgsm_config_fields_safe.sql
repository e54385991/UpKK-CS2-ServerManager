-- Safe migration script to add LGSM-style configuration fields to servers table
-- This script checks for column existence before adding them

DELIMITER $$

-- Procedure to add column if it doesn't exist
CREATE PROCEDURE AddColumnIfNotExists(
    IN tableName VARCHAR(100),
    IN columnName VARCHAR(100), 
    IN columnDefinition VARCHAR(255)
)
BEGIN
    DECLARE columnExists INT;
    
    SELECT COUNT(*) INTO columnExists
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = tableName
    AND COLUMN_NAME = columnName;
    
    IF columnExists = 0 THEN
        SET @sql = CONCAT('ALTER TABLE ', tableName, ' ADD COLUMN ', columnName, ' ', columnDefinition);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

DELIMITER ;

-- Add columns using the procedure
CALL AddColumnIfNotExists('servers', 'server_name', "VARCHAR(255) DEFAULT 'CS2 Server'");
CALL AddColumnIfNotExists('servers', 'server_password', 'VARCHAR(255) DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'rcon_password', 'VARCHAR(255) DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'default_map', "VARCHAR(100) DEFAULT 'de_dust2'");
CALL AddColumnIfNotExists('servers', 'max_players', 'INTEGER DEFAULT 32');
CALL AddColumnIfNotExists('servers', 'tickrate', 'INTEGER DEFAULT 128');
CALL AddColumnIfNotExists('servers', 'game_mode', "VARCHAR(50) DEFAULT 'competitive'");
CALL AddColumnIfNotExists('servers', 'game_type', "VARCHAR(50) DEFAULT '0'");
CALL AddColumnIfNotExists('servers', 'additional_parameters', 'TEXT DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'ip_address', 'VARCHAR(100) DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'client_port', 'INTEGER DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'tv_port', 'INTEGER DEFAULT NULL');
CALL AddColumnIfNotExists('servers', 'tv_enable', 'BOOLEAN DEFAULT FALSE');

-- Clean up
DROP PROCEDURE IF EXISTS AddColumnIfNotExists;

-- Update existing servers to have default values
UPDATE servers 
SET 
    server_name = COALESCE(server_name, 'CS2 Server'),
    default_map = COALESCE(default_map, 'de_dust2'),
    max_players = COALESCE(max_players, 32),
    tickrate = COALESCE(tickrate, 128),
    game_mode = COALESCE(game_mode, 'competitive'),
    game_type = COALESCE(game_type, '0'),
    tv_enable = COALESCE(tv_enable, FALSE)
WHERE server_name IS NULL OR default_map IS NULL;
