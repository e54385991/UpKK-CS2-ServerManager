/*
 Navicat Premium Data Transfer

 Source Server Type    : MySQL
 Source Server Version : 80407 (8.4.7)
 Source Schema         : cs2_manager

 Target Server Type    : MySQL
 Target Server Version : 80407 (8.4.7)
 File Encoding         : 65001

 Date: 05/12/2025 15:33:25
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for deployment_logs
-- ----------------------------
DROP TABLE IF EXISTS `deployment_logs`;
CREATE TABLE `deployment_logs`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `server_id` int NOT NULL,
  `action` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `status` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `output` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_deployment_logs_server_id`(`server_id` ASC) USING BTREE,
  INDEX `ix_deployment_logs_id`(`id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 62 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for initialized_servers
-- ----------------------------
DROP TABLE IF EXISTS `initialized_servers`;
CREATE TABLE `initialized_servers`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `host` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_port` int NULL DEFAULT NULL,
  `ssh_user` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `game_directory` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  `updated_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_initialized_servers_user_id`(`user_id` ASC) USING BTREE,
  INDEX `ix_initialized_servers_id`(`id` ASC) USING BTREE,
  CONSTRAINT `initialized_servers_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for market_plugins
-- ----------------------------
DROP TABLE IF EXISTS `market_plugins`;
CREATE TABLE `market_plugins`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `github_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL,
  `author` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL,
  `version` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL,
  `category` enum('GAME_MODE','ENTERTAINMENT','UTILITY','ADMIN','PERFORMANCE','LIBRARY','OTHER') CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'OTHER',
  `tags` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL,
  `is_recommended` tinyint(1) NULL DEFAULT 0,
  `icon_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL,
  `download_count` int NULL DEFAULT 0,
  `install_count` int NULL DEFAULT 0,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `dependencies` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL,
  `custom_install_path` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT 'Custom extraction path for non-standard packages (e.g., \"addons\")',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `github_url`(`github_url` ASC) USING BTREE,
  INDEX `idx_market_plugins_github_url`(`github_url` ASC) USING BTREE,
  INDEX `idx_market_plugins_title`(`title` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 7 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for monitoring_logs
-- ----------------------------
DROP TABLE IF EXISTS `monitoring_logs`;
CREATE TABLE `monitoring_logs`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `server_id` int NOT NULL,
  `event_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `status` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_monitoring_logs_created_at`(`created_at` ASC) USING BTREE,
  INDEX `ix_monitoring_logs_server_id`(`server_id` ASC) USING BTREE,
  INDEX `ix_monitoring_logs_id`(`id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1994 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for password_reset_tokens
-- ----------------------------
DROP TABLE IF EXISTS `password_reset_tokens`;
CREATE TABLE `password_reset_tokens`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `expires_at` datetime NOT NULL,
  `used` tinyint(1) NOT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `ix_password_reset_tokens_token`(`token` ASC) USING BTREE,
  INDEX `ix_password_reset_tokens_user_id`(`user_id` ASC) USING BTREE,
  INDEX `ix_password_reset_tokens_id`(`id` ASC) USING BTREE,
  CONSTRAINT `password_reset_tokens_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 9 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for scheduled_tasks
-- ----------------------------
DROP TABLE IF EXISTS `scheduled_tasks`;
CREATE TABLE `scheduled_tasks`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `server_id` int NOT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `action` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `enabled` tinyint(1) NULL DEFAULT NULL,
  `schedule_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `schedule_value` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `last_run` datetime NULL DEFAULT NULL,
  `next_run` datetime NULL DEFAULT NULL,
  `run_count` int NULL DEFAULT NULL,
  `last_status` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `last_error` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  `updated_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_scheduled_tasks_id`(`id` ASC) USING BTREE,
  INDEX `ix_scheduled_tasks_server_id`(`server_id` ASC) USING BTREE,
  CONSTRAINT `scheduled_tasks_ibfk_1` FOREIGN KEY (`server_id`) REFERENCES `servers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 2 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for servers
-- ----------------------------
DROP TABLE IF EXISTS `servers`;
CREATE TABLE `servers`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `host` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_port` int NULL DEFAULT NULL,
  `ssh_user` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `auth_type` enum('PASSWORD','KEY_FILE') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `ssh_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `ssh_key_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `sudo_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `game_port` int NULL DEFAULT NULL,
  `game_directory` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `status` enum('PENDING','DEPLOYING','RUNNING','STOPPED','ERROR','UNKNOWN') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `server_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `server_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `rcon_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `default_map` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `max_players` int NULL DEFAULT NULL,
  `tickrate` int NULL DEFAULT NULL,
  `game_mode` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `game_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `additional_parameters` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `ip_address` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `client_port` int NULL DEFAULT NULL,
  `tv_port` int NULL DEFAULT NULL,
  `tv_enable` tinyint(1) NULL DEFAULT NULL,
  `api_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `backend_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `auto_clear_crash_hours` int NULL DEFAULT NULL,
  `last_status_check` datetime NULL DEFAULT NULL,
  `enable_panel_monitoring` tinyint(1) NULL DEFAULT NULL,
  `monitor_interval_seconds` int NULL DEFAULT NULL,
  `auto_restart_on_crash` tinyint(1) NULL DEFAULT NULL,
  `a2s_query_host` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `a2s_query_port` int NULL DEFAULT NULL,
  `enable_a2s_monitoring` tinyint(1) NULL DEFAULT NULL,
  `a2s_failure_threshold` int NULL DEFAULT NULL,
  `a2s_check_interval_seconds` int NULL DEFAULT NULL,
  `current_game_version` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `enable_auto_update` tinyint(1) NULL DEFAULT NULL,
  `update_check_interval_hours` int NULL DEFAULT NULL,
  `last_update_check` datetime NULL DEFAULT NULL,
  `last_update_time` datetime NULL DEFAULT NULL,
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `last_deployed` datetime NULL DEFAULT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  `updated_at` datetime NULL DEFAULT 'now()',
  `cpu_affinity` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `github_proxy` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT 'GitHub proxy URL for plugin installation (e.g., https://ghfast.top/https://github.com)',
  `use_panel_proxy` tinyint(1) NOT NULL DEFAULT 0 COMMENT 'Use panel server as proxy for all downloads (SteamCMD, GitHub plugins). Mutually exclusive with github_proxy.',
  `steam_account_token` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `last_ssh_success` timestamp NULL DEFAULT NULL,
  `last_ssh_failure` timestamp NULL DEFAULT NULL,
  `consecutive_ssh_failures` int NULL DEFAULT 0,
  `is_ssh_down` tinyint(1) NULL DEFAULT 0,
  `enable_ssh_health_monitoring` tinyint(1) NULL DEFAULT 1,
  `ssh_health_check_interval_hours` int NULL DEFAULT 2,
  `ssh_health_failure_threshold` int NULL DEFAULT 84,
  `last_ssh_health_check` timestamp NULL DEFAULT NULL,
  `ssh_health_status` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT 'unknown',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `ix_servers_api_key`(`api_key` ASC) USING BTREE,
  INDEX `ix_servers_user_id`(`user_id` ASC) USING BTREE,
  INDEX `ix_servers_name`(`name` ASC) USING BTREE,
  INDEX `ix_servers_id`(`id` ASC) USING BTREE,
  CONSTRAINT `servers_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 5 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for ssh_servers_sudo
-- ----------------------------
DROP TABLE IF EXISTS `ssh_servers_sudo`;
CREATE TABLE `ssh_servers_sudo`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `host` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `ssh_port` int NOT NULL DEFAULT 22,
  `sudo_user` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `sudo_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `unique_ssh_sudo_config`(`user_id` ASC, `host` ASC, `ssh_port` ASC, `sudo_user` ASC) USING BTREE,
  INDEX `idx_ssh_servers_sudo_user_id`(`user_id` ASC) USING BTREE,
  CONSTRAINT `ssh_servers_sudo_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for system_settings
-- ----------------------------
DROP TABLE IF EXISTS `system_settings`;
CREATE TABLE `system_settings`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `default_proxy_mode` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `github_proxy_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `email_enabled` tinyint(1) NOT NULL,
  `email_provider` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `email_from_address` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `email_from_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `gmail_credentials_json` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `gmail_token_json` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  `smtp_host` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `smtp_port` int NULL DEFAULT NULL,
  `smtp_username` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `smtp_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `smtp_use_tls` tinyint(1) NOT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  `updated_at` datetime NULL DEFAULT 'now()',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_system_settings_id`(`id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for users
-- ----------------------------
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users`  (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `email` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `hashed_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `is_active` tinyint(1) NULL DEFAULT NULL,
  `is_admin` tinyint(1) NULL DEFAULT NULL,
  `created_at` datetime NULL DEFAULT 'now()',
  `updated_at` datetime NULL DEFAULT 'now()',
  `api_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `steam_api_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `google_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  `oauth_provider` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `ix_users_email`(`email` ASC) USING BTREE,
  UNIQUE INDEX `ix_users_username`(`username` ASC) USING BTREE,
  UNIQUE INDEX `idx_user_api_key`(`api_key` ASC) USING BTREE,
  INDEX `ix_users_id`(`id` ASC) USING BTREE,
  UNIQUE INDEX `idx_user_google_id`(`google_id` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 12 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

SET FOREIGN_KEY_CHECKS = 1;
