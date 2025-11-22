-- Migration script to add LGSM-style configuration fields to servers table
-- Run this on existing databases to add the new fields

-- Note: MySQL doesn't support IF NOT EXISTS in ALTER TABLE ADD COLUMN
-- This script will fail if columns already exist, which is expected behavior
-- If you need to re-run this script, drop the columns first or use a different approach

ALTER TABLE servers 
ADD COLUMN server_name VARCHAR(255) DEFAULT 'CS2 Server',
ADD COLUMN server_password VARCHAR(255) DEFAULT NULL,
ADD COLUMN rcon_password VARCHAR(255) DEFAULT NULL,
ADD COLUMN default_map VARCHAR(100) DEFAULT 'de_dust2',
ADD COLUMN max_players INTEGER DEFAULT 32,
ADD COLUMN tickrate INTEGER DEFAULT 128,
ADD COLUMN game_mode VARCHAR(50) DEFAULT 'competitive',
ADD COLUMN game_type VARCHAR(50) DEFAULT '0',
ADD COLUMN additional_parameters TEXT DEFAULT NULL,
ADD COLUMN ip_address VARCHAR(100) DEFAULT NULL,
ADD COLUMN client_port INTEGER DEFAULT NULL,
ADD COLUMN tv_port INTEGER DEFAULT NULL,
ADD COLUMN tv_enable BOOLEAN DEFAULT FALSE;

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
