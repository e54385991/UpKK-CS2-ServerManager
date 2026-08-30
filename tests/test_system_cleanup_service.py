#!/usr/bin/env python3
"""Unit tests for Linux system-junk cleanup and log-retention policy helpers."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.system_cleanup_service import (
    SystemCleanupService,
    can_apply_target,
    manual_execute_commands,
    manual_setup_commands,
    normalize_schedule_value,
    normalize_targets,
    parse_size,
    target_command,
)


class SystemCleanupHelpersTests(unittest.TestCase):
    def test_normalize_targets_rejects_unknown_and_deduplicates(self):
        self.assertEqual(normalize_targets(["journal", "journal", "tmp"]), ["journal", "tmp"])
        with self.assertRaises(ValueError):
            normalize_targets(["rm -rf /"])

    def test_schedule_and_size_parsers(self):
        self.assertEqual(normalize_schedule_value("3:30"), "03:30")
        with self.assertRaises(ValueError):
            normalize_schedule_value("25:00")
        self.assertEqual(parse_size("4096"), 4096)
        self.assertEqual(parse_size("Archived journals take up 1.5G on disk."), 1610612736)

    def test_privilege_and_manual_commands_stay_on_allowlist(self):
        self.assertTrue(can_apply_target("thumbnails", "none"))
        self.assertFalse(can_apply_target("journal", "none"))
        self.assertTrue(can_apply_target("journal", "sudo"))
        execute = "\n".join(manual_execute_commands(["journal", "apt_cache"], 7))
        self.assertIn("journalctl --vacuum-time=7d", execute)
        self.assertIn("sudo sh -c", execute)
        self.assertNotIn(";", target_command("journal", 7))
        setup = "\n".join(manual_setup_commands(["journal"], 7, "03:30"))
        self.assertIn("sudo crontab -e", setup)
        self.assertIn("30 3 * * *", setup)


class SystemCleanupApplyTests(unittest.TestCase):
    def test_apply_skips_privileged_targets_without_sudo(self):
        service = SystemCleanupService()

        async def execute_command(command, timeout=30):
            if command == "whoami":
                return True, "cs2server\n", ""
            if "sudo -n true" in command:
                return False, "", "sudo: a password is required"
            return True, "", ""

        ssh = SimpleNamespace(
            conn=object(),
            execute_command=execute_command,
            execute_sudo_command=AsyncMock(
                return_value=(False, "", "sudo: a password is required")
            ),
        )
        server = SimpleNamespace(
            game_directory="/home/cs2server/cs2",
            sudo_password=None,
            ssh_password=None,
            cleanup_retain_days=7,
        )

        result = asyncio.run(service.apply(ssh, server, ["journal", "thumbnails"]))

        self.assertFalse(result["success"])
        self.assertEqual(result["privilege"], "none")
        self.assertIn("journal", [item["id"] for item in result["skipped"]])
        self.assertTrue(result["manual_execute"])
        self.assertTrue(result["manual_setup"])
        self.assertIn("thumbnails", result["applied"])


if __name__ == "__main__":
    unittest.main()
