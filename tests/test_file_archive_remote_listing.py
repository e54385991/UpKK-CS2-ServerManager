#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
import shlex
import unittest
from unittest.mock import patch

from services.ssh_manager import SSHManager

if __name__ == "__main__":
    unittest.main()

from tests.file_manager_archive_fakes import (
    _ArchiveByteStream as _ArchiveByteStream,
)
from tests.file_manager_archive_fakes import (
    _ArchiveListingConnection as _ArchiveListingConnection,
)
from tests.file_manager_archive_fakes import (
    _ArchiveListingProcess as _ArchiveListingProcess,
)
from tests.file_manager_archive_fakes import (
    _StubbornArchiveListingProcess as _StubbornArchiveListingProcess,
)
from tests.file_manager_archive_fakes import (
    _TaskSSHManager as _TaskSSHManager,
)


class ArchiveRemoteCommandTests(unittest.TestCase):
    @staticmethod
    def run_async(coroutine):
        return asyncio.run(coroutine)

    def manager_with_commands(self, tool, responses):
        manager = SSHManager(use_pool=False)
        manager.conn = object()
        manager.tool_candidates = []
        manager.commands = []
        queued_responses = list(responses)

        async def find_tool(candidates):
            manager.tool_candidates.append(candidates)
            return tool

        async def execute(command, timeout=30):
            manager.commands.append((command, timeout))
            if queued_responses:
                return queued_responses.pop(0)
            return True, "", ""

        async def stream_listing(command, line_handler):
            manager.commands.append((command, SSHManager.ARCHIVE_INSPECT_TIMEOUT))
            if queued_responses:
                success, stdout, stderr = queued_responses.pop(0)
            else:
                success, stdout, stderr = True, "", ""
            if not success:
                return False, stderr or stdout or "Remote command failed"
            for line in stdout.splitlines():
                error = line_handler(line)
                if error:
                    return False, error
            return True, ""

        manager._find_remote_tool = find_tool
        manager.execute_command = execute
        manager._stream_archive_listing = stream_listing
        return manager

    def test_find_remote_tool_uses_fixed_quoted_candidates_in_order(self):
        manager = SSHManager(use_pool=False)
        manager.conn = object()
        commands = []

        async def execute(command, timeout=30):
            commands.append((command, timeout))
            if command.endswith(" 7z"):
                return True, "/usr/bin/7z\n", ""
            return False, "", "missing"

        manager.execute_command = execute
        result = self.run_async(manager._find_remote_tool(("7zz", "7z", "7za")))

        self.assertEqual(result, "/usr/bin/7z")
        self.assertEqual(
            commands,
            [("command -v 7zz", 5), ("command -v 7z", 5)],
        )

    def test_zip_inspection_selects_unzip_and_quotes_archive_path(self):
        archive_path = "/srv/game/release's build.zip"
        manager = self.manager_with_commands(
            "/usr/bin/unzip",
            [
                (True, "addons/\naddons/plugin.dll\n", ""),
                (True, "-rw-r--r--  1 user group 10 file\n", ""),
            ],
        )

        success, info, error = self.run_async(
            manager._inspect_archive_connected(archive_path, "zip")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["addons"])
        self.assertEqual(manager.tool_candidates, [("unzip",)])
        self.assertIn(shlex.quote(archive_path), manager.commands[0][0])
        self.assertIn(" -Z1 ", manager.commands[0][0])
        self.assertIn(" -Z -l ", manager.commands[1][0])

    def test_tar_xz_inspection_uses_xz_listing_flags(self):
        manager = self.manager_with_commands(
            "/bin/tar",
            [
                (
                    True,
                    'drwxr-xr-x 0/0 0 2026-07-15 04:00:00 "addons/"\n'
                    '-rw-r--r-- 0/0 1 2026-07-15 04:00:00 "addons/plugin.dll"\n',
                    "",
                ),
            ],
        )

        success, _, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.tar.xz", "tar.xz")
        )

        self.assertTrue(success, error)
        self.assertEqual(manager.tool_candidates, [("tar",)])
        self.assertIn(" -tvJf ", manager.commands[0][0])
        self.assertTrue(all("--quoting-style=c" in command for command, _ in manager.commands))
        self.assertIn("--numeric-owner", manager.commands[0][0])
        self.assertIn("--full-time", manager.commands[0][0])
        self.assertIn("--utc", manager.commands[0][0])
        self.assertIn("TAR_OPTIONS=", manager.commands[0][0])
        self.assertTrue(
            all(timeout == SSHManager.ARCHIVE_INSPECT_TIMEOUT for _, timeout in manager.commands)
        )

    def test_tar_inspection_decodes_windows_separators_and_uses_member_types(self):
        manager = self.manager_with_commands(
            "/bin/tar",
            [
                (
                    True,
                    'drwxr-xr-x 0/0 0 2026-07-15 04:00:00 "backup\\\\cfg/"\n'
                    "-rw-r--r-- 0/0 1 2026-07-15 04:00:00 "
                    '"backup\\\\cfg\\\\server.cfg"\n',
                    "",
                ),
            ],
        )

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/backup.tar.gz", "tar.gz")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["backup", "backup/cfg"])
        self.assertTrue(info["has_backslash_separators"])

    def test_7z_inspection_prefers_modern_then_legacy_tools(self):
        listing = """
----------
Path = addons
Folder = +
Attributes = D
"""
        manager = self.manager_with_commands(
            "/usr/bin/7za",
            [(True, listing, "")],
        )

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.7z", "7z")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["addons"])
        self.assertEqual(manager.tool_candidates, [("7zz", "7z", "7za")])
        self.assertIn(" l -slt -sccUTF-8 ", manager.commands[0][0])

    def test_rar_inspection_uses_7z_listing(self):
        listing = """
----------
Path = addons
Folder = +
Attributes = D
"""
        manager = self.manager_with_commands(
            "/usr/bin/7z",
            [(True, listing, "")],
        )

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.rar", "rar")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["archive_type"], "rar")
        self.assertEqual(info["folders"], ["addons"])
        self.assertEqual(manager.tool_candidates, [("7zz", "7z", "7za")])

    def test_tar_zst_inspection_requires_zstd_and_uses_compress_program(self):
        manager = SSHManager(use_pool=False)
        manager.conn = object()
        manager.tool_candidates = []
        manager.commands = []

        async def find_tool(candidates):
            manager.tool_candidates.append(candidates)
            if candidates == ("tar",):
                return "/bin/tar"
            if candidates == ("zstd",):
                return "/usr/bin/zstd"
            return None

        async def stream_listing(command, line_handler):
            manager.commands.append((command, SSHManager.ARCHIVE_INSPECT_TIMEOUT))
            for line in (
                'drwxr-xr-x 0/0 0 2026-07-15 04:00:00 "addons/"',
                '-rw-r--r-- 0/0 1 2026-07-15 04:00:00 "addons/plugin.dll"',
            ):
                error = line_handler(line)
                if error:
                    return False, error
            return True, ""

        manager._find_remote_tool = find_tool
        manager._stream_archive_listing = stream_listing

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.tar.zst", "tar.zst")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["addons"])
        self.assertEqual(manager.tool_candidates, [("tar",), ("zstd",)])
        self.assertIn("-I", shlex.split(manager.commands[0][0]))
        self.assertIn("/usr/bin/zstd", shlex.split(manager.commands[0][0]))
        self.assertIn("-tvf", shlex.split(manager.commands[0][0]))

    def test_inspection_reports_missing_required_tool_without_running_archive(self):
        manager = self.manager_with_commands(None, [])

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.zip", "zip")
        )

        self.assertFalse(success)
        self.assertEqual(info, {})
        self.assertIn("install unzip", error)
        self.assertEqual(manager.commands, [])


class ArchiveListingStreamTests(unittest.TestCase):
    @staticmethod
    def run_async(coroutine):
        return asyncio.run(coroutine)

    def test_streaming_listing_handles_chunk_boundaries_without_collecting_stdout(self):
        process = _ArchiveListingProcess(
            [b"first member\nsecond", b" member\nthird member", b""],
        )
        manager = SSHManager(use_pool=False)
        manager.conn = _ArchiveListingConnection(process)
        lines = []

        success, error = self.run_async(
            manager._stream_archive_listing(
                "tar-list-command",
                lambda line: lines.append(line),
            )
        )

        self.assertTrue(success, error)
        self.assertEqual(lines, ["first member", "second member", "third member"])
        self.assertEqual(
            manager.conn.calls,
            [("tar-list-command", {"encoding": None})],
        )

    def test_streaming_listing_stops_at_entry_limit(self):
        process = _StubbornArchiveListingProcess([b"one\ntwo\n"])
        manager = SSHManager(use_pool=False)
        manager.conn = _ArchiveListingConnection(process)

        with (
            patch.object(SSHManager, "ARCHIVE_MAX_ENTRIES", 1),
            patch.object(SSHManager, "ARCHIVE_LISTING_STOP_TIMEOUT", 0.01),
        ):
            success, error = self.run_async(
                manager._stream_archive_listing("tar-list-command", lambda _line: None)
            )

        self.assertFalse(success)
        self.assertIn("too many entries", error)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_streaming_listing_rejects_oversized_unterminated_line(self):
        process = _ArchiveListingProcess([b"x" * 33])
        manager = SSHManager(use_pool=False)
        manager.conn = _ArchiveListingConnection(process)

        with patch.object(SSHManager, "ARCHIVE_LISTING_MAX_LINE_BYTES", 32):
            success, error = self.run_async(
                manager._stream_archive_listing("tar-list-command", lambda _line: None)
            )

        self.assertFalse(success)
        self.assertIn("long", error)
        self.assertTrue(process.terminated)

    def test_streaming_listing_bounds_remote_error_output(self):
        process = _ArchiveListingProcess(
            [],
            stderr_chunks=[b"failure:" + (b"x" * 100)],
            exit_status=2,
        )
        manager = SSHManager(use_pool=False)
        manager.conn = _ArchiveListingConnection(process)

        with patch.object(SSHManager, "ARCHIVE_LISTING_ERROR_BYTES", 16):
            success, error = self.run_async(
                manager._stream_archive_listing("tar-list-command", lambda _line: None)
            )

        self.assertFalse(success)
        self.assertEqual(error, "failure:xxxxxxxx")
