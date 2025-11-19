-- Migration: Add monitoring logs table
-- Date: 2025-11-18
-- Description: Creates monitoring_logs table to store panel monitoring events and auto-restart activities

CREATE TABLE IF NOT EXISTS monitoring_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    server_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL COMMENT 'Type: status_check, auto_restart, monitoring_start, monitoring_stop',
    status VARCHAR(50) NOT NULL COMMENT 'Status: success, failed, info, warning',
    message TEXT NOT NULL COMMENT 'Log message describing the event',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_server_id (server_id),
    INDEX idx_created_at (created_at),
    INDEX idx_event_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Panel monitoring logs - stores monitoring events and auto-restart activities';
