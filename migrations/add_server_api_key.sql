-- Migration script to add api_key field to servers table
-- This field is used for secure server-to-backend communication for status reporting

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

-- Add api_key column
CALL AddColumnIfNotExists('servers', 'api_key', 'VARCHAR(64) DEFAULT NULL');

-- Add unique index on api_key if it doesn't exist
SET @indexExists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'servers'
    AND INDEX_NAME = 'idx_server_api_key'
);

SET @sql = IF(@indexExists = 0,
    'CREATE UNIQUE INDEX idx_server_api_key ON servers(api_key)',
    'SELECT "Index idx_server_api_key already exists"'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Clean up
DROP PROCEDURE IF EXISTS AddColumnIfNotExists;
