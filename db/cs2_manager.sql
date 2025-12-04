-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- 主机： 1Panel-mysql-KZBC
-- 生成日期： 2025-12-04 15:09:36
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
-- 表的结构 `market_plugins`
--

CREATE TABLE `market_plugins` (
  `id` int NOT NULL,
  `github_url` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `title` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `author` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `version` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `category` enum('GAME_MODE','ENTERTAINMENT','UTILITY','ADMIN','PERFORMANCE','LIBRARY','OTHER') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'OTHER',
  `tags` text COLLATE utf8mb4_unicode_ci,
  `is_recommended` tinyint(1) DEFAULT '0',
  `icon_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `download_count` int DEFAULT '0',
  `install_count` int DEFAULT '0',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `dependencies` text COLLATE utf8mb4_unicode_ci,
  `custom_install_path` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Custom extraction path for non-standard packages (e.g., "addons")'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
-- 表的结构 `scheduled_tasks`
--

CREATE TABLE `scheduled_tasks` (
  `id` int NOT NULL,
  `server_id` int NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `action` varchar(50) COLLATE utf8mb4_general_ci NOT NULL,
  `enabled` tinyint(1) DEFAULT NULL,
  `schedule_type` varchar(50) COLLATE utf8mb4_general_ci NOT NULL,
  `schedule_value` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `last_run` datetime DEFAULT NULL,
  `next_run` datetime DEFAULT NULL,
  `run_count` int DEFAULT NULL,
  `last_status` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `last_error` text COLLATE utf8mb4_general_ci,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now())
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

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
  `update_check_interval_hours` int DEFAULT '1' COMMENT 'Hours between version checks (1-24)',
  `cpu_affinity` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `github_proxy` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT 'GitHub proxy URL for plugin installation (e.g., https://ghfast.top/https://github.com)',
  `use_panel_proxy` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Use panel server as proxy for all downloads (SteamCMD, GitHub plugins). Mutually exclusive with github_proxy.',
  `steam_account_token` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `last_ssh_success` timestamp NULL DEFAULT NULL,
  `last_ssh_failure` timestamp NULL DEFAULT NULL,
  `consecutive_ssh_failures` int DEFAULT '0',
  `is_ssh_down` tinyint(1) DEFAULT '0',
  `enable_ssh_health_monitoring` tinyint(1) DEFAULT '1',
  `ssh_health_check_interval_hours` int DEFAULT '2',
  `ssh_health_failure_threshold` int DEFAULT '84',
  `last_ssh_health_check` timestamp NULL DEFAULT NULL,
  `ssh_health_status` varchar(50) COLLATE utf8mb4_general_ci DEFAULT 'unknown'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `ssh_servers_sudo`
--

CREATE TABLE `ssh_servers_sudo` (
  `id` int NOT NULL,
  `user_id` int NOT NULL,
  `host` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ssh_port` int NOT NULL DEFAULT '22',
  `sudo_user` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sudo_password` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
  `updated_at` datetime DEFAULT (now()),
  `api_key` varchar(64) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `steam_api_key` varchar(64) COLLATE utf8mb4_general_ci DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `user_settings`
--

CREATE TABLE `user_settings` (
  `id` int NOT NULL,
  `user_id` int NOT NULL,
  `steamcmd_mirror_url` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `github_api_mirror_url` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `github_objects_mirror_url` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL,
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
-- 表的索引 `market_plugins`
--
ALTER TABLE `market_plugins`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `github_url` (`github_url`),
  ADD KEY `idx_market_plugins_github_url` (`github_url`),
  ADD KEY `idx_market_plugins_title` (`title`);

--
-- 表的索引 `monitoring_logs`
--
ALTER TABLE `monitoring_logs`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_server_id` (`server_id`),
  ADD KEY `idx_created_at` (`created_at`),
  ADD KEY `idx_event_type` (`event_type`);

--
-- 表的索引 `scheduled_tasks`
--
ALTER TABLE `scheduled_tasks`
  ADD PRIMARY KEY (`id`),
  ADD KEY `ix_scheduled_tasks_server_id` (`server_id`),
  ADD KEY `ix_scheduled_tasks_id` (`id`);

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
-- 表的索引 `ssh_servers_sudo`
--
ALTER TABLE `ssh_servers_sudo`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_ssh_sudo_config` (`user_id`,`host`,`ssh_port`,`sudo_user`),
  ADD KEY `idx_ssh_servers_sudo_user_id` (`user_id`);

--
-- 表的索引 `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `ix_users_username` (`username`),
  ADD UNIQUE KEY `ix_users_email` (`email`),
  ADD UNIQUE KEY `idx_user_api_key` (`api_key`),
  ADD KEY `ix_users_id` (`id`);

--
-- 表的索引 `user_settings`
--
ALTER TABLE `user_settings`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `ix_user_settings_user_id` (`user_id`),
  ADD KEY `ix_user_settings_id` (`id`);

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
-- 使用表AUTO_INCREMENT `market_plugins`
--
ALTER TABLE `market_plugins`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `monitoring_logs`
--
ALTER TABLE `monitoring_logs`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `scheduled_tasks`
--
ALTER TABLE `scheduled_tasks`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `servers`
--
ALTER TABLE `servers`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `ssh_servers_sudo`
--
ALTER TABLE `ssh_servers_sudo`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `users`
--
ALTER TABLE `users`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `user_settings`
--
ALTER TABLE `user_settings`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- 限制导出的表
--

--
-- 限制表 `initialized_servers`
--
ALTER TABLE `initialized_servers`
  ADD CONSTRAINT `initialized_servers_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`);

--
-- 限制表 `scheduled_tasks`
--
ALTER TABLE `scheduled_tasks`
  ADD CONSTRAINT `scheduled_tasks_ibfk_1` FOREIGN KEY (`server_id`) REFERENCES `servers` (`id`) ON DELETE CASCADE;

--
-- 限制表 `ssh_servers_sudo`
--
ALTER TABLE `ssh_servers_sudo`
  ADD CONSTRAINT `ssh_servers_sudo_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- 限制表 `user_settings`
--
ALTER TABLE `user_settings`
  ADD CONSTRAINT `user_settings_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
