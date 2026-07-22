#!/usr/bin/env python3
"""
Unit tests for game directory cleanup classification and deletion safeguards.
"""

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace


def load_cleanup_module():
    spec = importlib.util.spec_from_file_location(
        "game_cleanup_service_file",
        "services/game_cleanup_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup_module = load_cleanup_module()
GameCleanupService = cleanup_module.GameCleanupService
WORKSHOP_CONFIRMATION_TEXT = cleanup_module.WORKSHOP_CONFIRMATION_TEXT


class FakeSSHManager:
    def __init__(self, outputs=None):
        self.conn = object()
        self.outputs = list(outputs or [])
        self.commands = []
        self.deleted_paths = []

    async def connect(self, server):
        self.conn = object()
        return True, ""

    async def execute_command(self, command, timeout=30):
        self.commands.append(command)
        if not self.outputs:
            return True, "", ""
        return True, self.outputs.pop(0), ""

    async def delete_path(self, path, server):
        self.deleted_paths.append(path)
        return True, ""


class GameCleanupServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = GameCleanupService()
        self.server = SimpleNamespace(
            game_directory="/home/cs2server/cs2ze",
        )
        self.base = self.server.game_directory
        self.workshop = f"{self.base}/cs2/game/bin/linuxsteamrt64/steamapps/workshop"

    def record(self, file_type, size, path, modified=1000):
        return f"{file_type}\t{size}\t{modified}\t{path}\0"

    def test_path_safety_stays_inside_game_directory(self):
        self.assertTrue(
            self.service.is_path_safe(self.server, f"{self.base}/cs2/game/csgo/server.log")
        )
        self.assertFalse(self.service.is_path_safe(self.server, "/home/cs2server/other/server.log"))
        self.assertFalse(self.service.is_path_safe(self.server, f"{self.base}/../other/server.log"))
        self.assertFalse(self.service.is_path_safe(self.server, f"{self.base}/bad\npath.log"))

    def test_scan_classifies_candidates_and_excludes_symlink_and_workshop_archives(self):
        outputs = [
            self.record("f", 10, f"{self.base}/cs2/game/csgo/logs/server.log")
            + self.record("l", 0, f"{self.base}/cs2/game/csgo/logs/link"),
            "",
            self.record("f", 20, f"{self.workshop}/temp/download.tmp"),
            self.record("f", 30, f"{self.base}/leftover.zip")
            + self.record("f", 40, f"{self.workshop}/123/file.zip")
            + self.record("f", 50, f"{self.base}/cs2/game/csgo/console.log"),
            self.record("d", 60, f"{self.workshop}/123")
            + self.record("d", 20, f"{self.workshop}/temp"),
        ]
        ssh = FakeSSHManager(outputs)

        success, data, error = asyncio.run(self.service.scan(ssh, self.server))

        self.assertTrue(success, error)
        safe_paths = {item["path"] for item in data["safe_items"]}
        archive_paths = {item["path"] for item in data["archive_items"]}

        self.assertIn(f"{self.base}/cs2/game/csgo/logs/server.log", safe_paths)
        self.assertIn(f"{self.workshop}/temp/download.tmp", safe_paths)
        self.assertIn(f"{self.base}/cs2/game/csgo/console.log", safe_paths)
        self.assertNotIn(f"{self.base}/cs2/game/csgo/logs/link", safe_paths)
        self.assertEqual(archive_paths, {f"{self.base}/leftover.zip"})
        self.assertEqual(data["workshop_summary"]["item_count"], 2)
        self.assertEqual(data["workshop_summary"]["size"], 80)
        self.assertEqual(data["total_size"], 190)

    def test_direct_child_scan_uses_recursive_directory_size(self):
        ssh = FakeSSHManager(
            [
                self.record("d", 1024, f"{self.workshop}/content"),
            ]
        )

        success, records, error = asyncio.run(
            self.service._scan_direct_children(ssh, self.server, self.workshop)
        )

        self.assertTrue(success, error)
        self.assertEqual(records[0]["size"], 1024)
        self.assertIn("du -sb", ssh.commands[0])
        self.assertIn("-maxdepth 1", ssh.commands[0])

    def test_archive_delete_rejects_non_candidate_paths(self):
        async def fake_scan(ssh_manager, server):
            return (
                True,
                {
                    "safe_items": [],
                    "archive_items": [
                        {
                            "path": f"{self.base}/leftover.zip",
                            "name": "leftover.zip",
                            "type": "file",
                            "size": 30,
                            "modified": 1000,
                            "category": "archive",
                            "reason": "Common leftover archive file",
                            "danger_level": "confirm",
                        }
                    ],
                    "workshop_summary": {
                        "path": self.workshop,
                        "item_count": 0,
                        "size": 0,
                        "items": [],
                    },
                    "total_size": 30,
                },
                "",
            )

        self.service.scan = fake_scan
        ssh = FakeSSHManager()

        success, result, error = asyncio.run(
            self.service.delete(
                ssh, self.server, "archives", paths=[f"{self.base}/not-archive.txt"]
            )
        )

        self.assertFalse(success)
        self.assertIn("no longer valid", error)
        self.assertEqual(ssh.deleted_paths, [])

    def test_workshop_delete_requires_confirmation_and_keeps_root_directory(self):
        async def fake_scan(ssh_manager, server):
            return (
                True,
                {
                    "safe_items": [],
                    "archive_items": [],
                    "workshop_summary": {
                        "path": self.workshop,
                        "item_count": 2,
                        "size": 80,
                        "items": [
                            {
                                "path": f"{self.workshop}/123",
                                "name": "123",
                                "type": "directory",
                                "size": 60,
                                "modified": 1000,
                                "category": "workshop",
                                "reason": "Steam Workshop content",
                                "danger_level": "danger",
                            },
                            {
                                "path": f"{self.workshop}/temp",
                                "name": "temp",
                                "type": "directory",
                                "size": 20,
                                "modified": 1000,
                                "category": "workshop",
                                "reason": "Steam Workshop content",
                                "danger_level": "danger",
                            },
                        ],
                    },
                    "total_size": 80,
                },
                "",
            )

        self.service.scan = fake_scan

        ssh = FakeSSHManager()
        success, result, error = asyncio.run(
            self.service.delete(ssh, self.server, "workshop", confirmation_text="wrong")
        )
        self.assertFalse(success)
        self.assertIn(WORKSHOP_CONFIRMATION_TEXT, error)
        self.assertEqual(ssh.deleted_paths, [])

        ssh = FakeSSHManager()
        success, result, error = asyncio.run(
            self.service.delete(
                ssh, self.server, "workshop", confirmation_text=WORKSHOP_CONFIRMATION_TEXT
            )
        )
        self.assertTrue(success, error)
        self.assertEqual(ssh.deleted_paths, [f"{self.workshop}/123", f"{self.workshop}/temp"])
        self.assertNotIn(self.workshop, ssh.deleted_paths)


if __name__ == "__main__":
    unittest.main()
