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
        self.workshop = f"{self.base}/game/bin/linuxsteamrt64/steamapps/workshop"

    def record(self, file_type, size, path, modified=1000):
        return f"{file_type}\t{size}\t{modified}\t{path}\0"

    def test_css_and_log_roots_do_not_insert_extra_cs2(self):
        self.assertEqual(
            self.service.css_logs_dir(self.server),
            f"{self.base}/game/csgo/addons/counterstrikesharp/logs",
        )
        self.assertEqual(self.service.csgo_logs_dir(self.server), f"{self.base}/game/csgo/logs")
        self.assertNotIn("/cs2/game/", self.service.css_logs_dir(self.server))

    def test_path_safety_stays_inside_game_directory(self):
        self.assertTrue(self.service.is_path_safe(self.server, f"{self.base}/game/csgo/server.log"))
        self.assertFalse(self.service.is_path_safe(self.server, "/home/cs2server/other/server.log"))
        self.assertFalse(self.service.is_path_safe(self.server, f"{self.base}/../other/server.log"))
        self.assertFalse(self.service.is_path_safe(self.server, f"{self.base}/bad\npath.log"))

    def test_scan_classifies_candidates_and_excludes_symlink_and_workshop_archives(self):
        outputs = [
            self.record("f", 10, f"{self.base}/game/csgo/logs/server.log")
            + self.record("l", 0, f"{self.base}/game/csgo/logs/link"),
            "",
            self.record("f", 20, f"{self.workshop}/temp/download.tmp"),
            self.record("f", 50, f"{self.base}/game/csgo/console.log"),
            self.record("f", 30, f"{self.base}/leftover.zip")
            + self.record("f", 40, f"{self.workshop}/123/file.zip"),
            self.record("d", 60, f"{self.workshop}/123")
            + self.record("d", 20, f"{self.workshop}/temp"),
            "80\n",
        ]
        ssh = FakeSSHManager(outputs)

        success, data, error = asyncio.run(self.service.scan(ssh, self.server))

        self.assertTrue(success, error)
        safe_paths = {item["path"] for item in data["safe_items"]}
        archive_paths = {item["path"] for item in data["archive_items"]}

        self.assertIn(f"{self.base}/game/csgo/logs/server.log", safe_paths)
        self.assertIn(f"{self.workshop}/temp/download.tmp", safe_paths)
        self.assertIn(f"{self.base}/game/csgo/console.log", safe_paths)
        self.assertNotIn(f"{self.base}/game/csgo/logs/link", safe_paths)
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
        self.assertIn("-printf", ssh.commands[0])
        self.assertIn("-maxdepth 1", ssh.commands[0])
        self.assertNotIn("du -sb", ssh.commands[0])

    def test_archive_delete_rejects_non_candidate_paths(self):
        ssh = FakeSSHManager()

        success, result, error = asyncio.run(
            self.service.delete(
                ssh, self.server, "archives", paths=[f"{self.base}/not-archive.txt"]
            )
        )

        self.assertFalse(success)
        self.assertIn("no longer valid", error)
        self.assertEqual(ssh.deleted_paths, [])

    def test_scan_uses_named_finds_instead_of_listing_every_file(self):
        ssh = FakeSSHManager(["", "", "", "", "", "", "0\n"])
        asyncio.run(self.service.scan(ssh, self.server))
        joined = "\n".join(ssh.commands)
        self.assertIn("-iname '*.log'", joined)
        self.assertIn("-iname '*.zip'", joined)
        self.assertIn("-prune", joined)
        self.assertNotIn("-o -type f -printf", joined.replace(" ", ""))

    def test_workshop_delete_requires_confirmation_and_keeps_root_directory(self):
        children = self.record("d", 60, f"{self.workshop}/123") + self.record(
            "d", 20, f"{self.workshop}/temp"
        )

        ssh = FakeSSHManager()
        success, result, error = asyncio.run(
            self.service.delete(ssh, self.server, "workshop", confirmation_text="wrong")
        )
        self.assertFalse(success)
        self.assertIn(WORKSHOP_CONFIRMATION_TEXT, error)
        self.assertEqual(ssh.deleted_paths, [])

        ssh = FakeSSHManager([children])
        success, result, error = asyncio.run(
            self.service.delete(
                ssh, self.server, "workshop", confirmation_text=WORKSHOP_CONFIRMATION_TEXT
            )
        )
        self.assertTrue(success, error)
        self.assertEqual(ssh.deleted_paths, [f"{self.workshop}/123", f"{self.workshop}/temp"])
        self.assertNotIn(self.workshop, ssh.deleted_paths)

    def test_purge_old_logs_stays_inside_approved_roots(self):
        old_log = f"{self.base}/game/csgo/logs/old.log"
        ssh = FakeSSHManager(
            [
                self.record("f", 80, old_log),
                "",
                "",
            ]
        )

        success, result, error = asyncio.run(self.service.purge_old_logs(ssh, self.server, 7))

        self.assertTrue(success, error)
        self.assertEqual(ssh.deleted_paths, [old_log])
        self.assertIn("-mtime +7", ssh.commands[0])
        self.assertEqual(result["deleted_count"], 1)

    def test_iter_scan_emits_phases_before_done(self):
        ssh = FakeSSHManager(["", "", "", "", "", "", "0\n"])

        async def collect():
            return [event async for event in self.service.iter_scan(ssh, self.server)]

        events = asyncio.run(collect())
        kinds = [event["type"] for event in events]
        self.assertIn("phase", kinds)
        self.assertIn("batch", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertIn("logs", {event.get("phase") for event in events if event["type"] == "phase"})
        self.assertIn("safe_item_count", events[-1]["data"])
        self.assertFalse(events[-1]["data"]["truncated"])

    def test_parse_find_chunk_keeps_partial_records(self):
        first = f"f\t12\t1\t{self.base}/partial"
        records, carry = self.service._parse_find_chunk(self.server, first, "")
        self.assertEqual(records, [])
        self.assertTrue(carry.startswith("f\t12"))
        records, carry = self.service._parse_find_chunk(self.server, ".log\0", carry)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["path"], f"{self.base}/partial.log")
        self.assertEqual(carry, "")


if __name__ == "__main__":
    unittest.main()
