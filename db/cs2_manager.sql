-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- 主机： 1Panel-mysql-KZBC
-- 生成日期： 2025-11-22 05:57:23
-- 服务器版本： 8.4.7
-- PHP 版本： 8.3.27

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- 数据库： `cs2_manager`
--

-- --------------------------------------------------------

--
-- 表的结构 `deployment_logs`
--

CREATE TABLE `deployment_logs` (
  `id` int NOT NULL,
  `server_id` int NOT NULL,
  `action` varchar(50) COLLATE utf8mb4_general_ci NOT NULL,
  `status` varchar(50) COLLATE utf8mb4_general_ci NOT NULL,
  `output` text COLLATE utf8mb4_general_ci,
  `error_message` text COLLATE utf8mb4_general_ci,
  `created_at` datetime DEFAULT (now())
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `global_settings`
--

CREATE TABLE `global_settings` (
  `id` int NOT NULL,
  `setting_key` varchar(100) COLLATE utf8mb4_general_ci NOT NULL,
  `setting_value` text COLLATE utf8mb4_general_ci NOT NULL,
  `description` text COLLATE utf8mb4_general_ci,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now())
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `initialized_servers`
--

CREATE TABLE `initialized_servers` (
  `id` int NOT NULL,
  `user_id` int NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `host` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_port` int DEFAULT NULL,
  `ssh_user` varchar(100) COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_password` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `game_directory` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now())
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `monitoring_logs`
--

CREATE TABLE `monitoring_logs` (
  `id` int NOT NULL,
  `server_id` int NOT NULL,
  `event_type` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Type: status_check, auto_restart, monitoring_start, monitoring_stop',
  `status` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Status: success, failed, info, warning',
  `message` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Log message describing the event',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Panel monitoring logs - stores monitoring events and auto-restart activities';

-- --------------------------------------------------------

--
-- 表的结构 `servers`
--

CREATE TABLE `servers` (
  `id` int NOT NULL,
  `user_id` int NOT NULL DEFAULT '1',
  `name` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `host` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_port` int DEFAULT NULL,
  `ssh_user` varchar(100) COLLATE utf8mb4_general_ci NOT NULL,
  `auth_type` enum('PASSWORD','KEY_FILE') COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_password` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `ssh_key_path` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `sudo_password` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `game_port` int DEFAULT NULL,
  `game_directory` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `status` enum('PENDING','DEPLOYING','RUNNING','STOPPED','ERROR','UNKNOWN') COLLATE utf8mb4_general_ci DEFAULT NULL,
  `description` text COLLATE utf8mb4_general_ci,
  `last_deployed` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now()),
  `server_name` varchar(255) COLLATE utf8mb4_general_ci DEFAULT 'CS2 Server',
  `server_password` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `rcon_password` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `default_map` varchar(100) COLLATE utf8mb4_general_ci DEFAULT 'de_dust2',
  `max_players` int DEFAULT '32',
  `tickrate` int DEFAULT '128',
  `game_mode` varchar(50) COLLATE utf8mb4_general_ci DEFAULT 'competitive',
  `game_type` varchar(50) COLLATE utf8mb4_general_ci DEFAULT '0',
  `additional_parameters` text COLLATE utf8mb4_general_ci,
  `ip_address` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `client_port` int DEFAULT NULL,
  `tv_port` int DEFAULT NULL,
  `tv_enable` tinyint(1) DEFAULT '0',
  `auto_restart_enabled` tinyint(1) DEFAULT '1' COMMENT 'Enable automatic restart on server crash',
  `monitoring_interval` int DEFAULT '60' COMMENT 'Server status check interval in seconds',
  `api_key` varchar(64) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `backend_url` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `auto_clear_crash_hours` int DEFAULT NULL COMMENT 'Hours offline before auto-clearing crash history (0 or NULL = disabled, recommended: 2)',
  `last_status_check` datetime DEFAULT NULL COMMENT 'Last time server status was checked',
  `enable_panel_monitoring` tinyint(1) DEFAULT '0' COMMENT 'Enable web panel monitoring and auto-restart (independent of local autorestart)',
  `monitor_interval_seconds` int DEFAULT '60' COMMENT 'How often to check server status in seconds (10-3600, default: 60)',
  `auto_restart_on_crash` tinyint(1) DEFAULT '1' COMMENT 'Auto-restart if process not found (when panel monitoring enabled)',
  `a2s_query_host` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT 'Custom A2S query host (defaults to server host if not set)',
  `a2s_query_port` int DEFAULT NULL COMMENT 'Custom A2S query port (defaults to game port if not set)',
  `enable_a2s_monitoring` tinyint(1) DEFAULT '0' COMMENT 'Enable A2S query monitoring for auto-restart',
  `a2s_failure_threshold` int DEFAULT '3' COMMENT 'Number of consecutive A2S failures before triggering restart (1-10)',
  `a2s_check_interval_seconds` int DEFAULT '60' COMMENT 'A2S check interval in seconds (minimum: 15)',
  `current_game_version` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT 'Current installed CS2 version from A2S query or manual entry',
  `enable_auto_update` tinyint(1) DEFAULT '1' COMMENT 'Enable automatic updates based on Steam API version check',
  `last_update_check` datetime DEFAULT NULL COMMENT 'Last time version was checked against Steam API',
  `last_update_time` datetime DEFAULT NULL COMMENT 'Last time server was updated',
  `update_check_interval_hours` int DEFAULT '1' COMMENT 'Hours between version checks (1-24)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `users`
--

CREATE TABLE `users` (
  `id` int NOT NULL,
  `username` varchar(100) COLLATE utf8mb4_general_ci NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `hashed_password` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `is_admin` tinyint(1) DEFAULT NULL,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now())
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 转储表的索引
--

--
-- 表的索引 `deployment_logs`
--
ALTER TABLE `deployment_logs`
  ADD PRIMARY KEY (`id`),
  ADD KEY `ix_deployment_logs_server_id` (`server_id`),
  ADD KEY `ix_deployment_logs_id` (`id`);

--
-- 表的索引 `global_settings`
--
ALTER TABLE `global_settings`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `ix_global_settings_setting_key` (`setting_key`),
  ADD KEY `ix_global_settings_id` (`id`);

--
-- 表的索引 `initialized_servers`
--
ALTER TABLE `initialized_servers`
  ADD PRIMARY KEY (`id`),
  ADD KEY `ix_initialized_servers_user_id` (`user_id`),
  ADD KEY `ix_initialized_servers_id` (`id`);

--
-- 表的索引 `monitoring_logs`
--
ALTER TABLE `monitoring_logs`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_server_id` (`server_id`),
  ADD KEY `idx_created_at` (`created_at`),
  ADD KEY `idx_event_type` (`event_type`);

--
-- 表的索引 `servers`
--
ALTER TABLE `servers`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `ix_servers_name` (`name`),
  ADD UNIQUE KEY `idx_server_api_key` (`api_key`),
  ADD KEY `ix_servers_id` (`id`),
  ADD KEY `idx_servers_user_id` (`user_id`),
  ADD KEY `idx_servers_last_status_check` (`last_status_check`),
  ADD KEY `idx_servers_panel_monitoring` (`enable_panel_monitoring`),
  ADD KEY `idx_last_update_check` (`last_update_check`);

--
-- 表的索引 `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `ix_users_username` (`username`),
  ADD UNIQUE KEY `ix_users_email` (`email`),
  ADD KEY `ix_users_id` (`id`);

--
-- 在导出的表使用AUTO_INCREMENT
--

--
-- 使用表AUTO_INCREMENT `deployment_logs`
--
ALTER TABLE `deployment_logs`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `global_settings`
--
ALTER TABLE `global_settings`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `initialized_servers`
--
ALTER TABLE `initialized_servers`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `monitoring_logs`
--
ALTER TABLE `monitoring_logs`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `servers`
--
ALTER TABLE `servers`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `users`
--
ALTER TABLE `users`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 限制导出的表
--

--
-- 限制表 `initialized_servers`
--
ALTER TABLE `initialized_servers`
  ADD CONSTRAINT `initialized_servers_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
