#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

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


class ArchivePureHelperTests(unittest.TestCase):
    def test_archive_type_mapping_prefers_compound_suffixes_and_ignores_case(self):
        cases = {
            "bundle.zip": "zip",
            "bundle.7Z": "7z",
            "bundle.rar": "rar",
            "bundle.tar": "tar",
            "bundle.TAR.GZ": "tar.gz",
            "bundle.tgz": "tar.gz",
            "bundle.tar.bz2": "tar.bz2",
            "bundle.tbz2": "tar.bz2",
            "bundle.tbz": "tar.bz2",
            "bundle.tar.xz": "tar.xz",
            "bundle.txz": "tar.xz",
            "bundle.tar.zst": "tar.zst",
            "bundle.tzst": "tar.zst",
            "bundle.tar.lzma": "tar.lzma",
            "bundle.tlz": "tar.lzma",
            "bundle.gz": "gz",
            "bundle.bz2": "bz2",
            "bundle.xz": "xz",
            "bundle.zst": "zst",
            "bundle.zstd": "zst",
            "bundle.lzma": "lzma",
            "bundle.zip.txt": None,
            "bundle.iso": None,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(SSHManager.archive_type_from_path(path), expected)

    def test_redirected_download_prefers_utf8_content_disposition_filename(self):
        headers = (
            "HTTP/1.1 302 Found\r\n"
            'Content-Disposition: attachment; filename="redirect.zip"\r\n\r\n'
            "HTTP/1.1 200 OK\r\n"
            "Content-Disposition: attachment; filename=legacy.zip; "
            "filename*=UTF-8''CS2Fixes%20Linux.zip\r\n\r\n"
        )
        filename, error = SSHManager._filename_from_download_response(
            headers,
            "https://objects.example.com/opaque-token",
        )
        self.assertEqual(filename, "CS2Fixes Linux.zip")
        self.assertEqual(error, "")

    def test_redirected_download_accepts_plain_content_disposition_filename(self):
        headers = (
            'HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename="artifact.7z"\r\n\r\n'
        )
        filename, error = SSHManager._filename_from_download_response(
            headers,
            "https://objects.example.com/opaque-token",
        )
        self.assertEqual(filename, "artifact.7z")
        self.assertEqual(error, "")

    def test_redirected_download_falls_back_to_effective_url_path(self):
        filename, error = SSHManager._filename_from_download_response(
            "HTTP/2 200\r\nContent-Type: application/octet-stream\r\n\r\n",
            "https://cdn.example.com/releases/release%20build.tar.gz?signature=1",
        )
        self.assertEqual(filename, "release build.tar.gz")
        self.assertEqual(error, "")

    def test_archive_member_normalization_accepts_safe_posix_members(self):
        self.assertEqual(
            SSHManager._normalize_archive_member("./addons/plugins/example.dll"),
            ("addons/plugins/example.dll", None),
        )
        self.assertEqual(
            SSHManager._normalize_archive_member("addons/plugins/"),
            ("addons/plugins", None),
        )
        self.assertEqual(SSHManager._normalize_archive_member("."), (None, None))
        self.assertEqual(SSHManager._normalize_archive_member("./"), (None, None))

    def test_archive_member_normalization_accepts_safe_windows_tar_members(self):
        self.assertEqual(
            SSHManager._normalize_archive_member(
                r".\addons\plugins\example.dll",
                allow_backslash_separators=True,
            ),
            ("addons/plugins/example.dll", None),
        )
        self.assertEqual(
            SSHManager._normalize_archive_member(
                ".\\",
                allow_backslash_separators=True,
            ),
            (None, None),
        )

    def test_windows_tar_member_normalization_still_rejects_unsafe_paths(self):
        unsafe_members = (
            "\\",
            r"\etc\passwd",
            r"..\outside",
            r"addons\..\outside",
            r"C:\Windows\system.ini",
            r"\\server\share\file.txt",
            r"addons\\plugins\example.dll",
        )
        for member in unsafe_members:
            with self.subTest(member=repr(member)):
                normalized, error = SSHManager._normalize_archive_member(
                    member,
                    allow_backslash_separators=True,
                )
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_tar_listing_escape_decoder_preserves_backslashes_and_controls(self):
        self.assertEqual(
            SSHManager._decode_tar_listing_name(r"addons\\plugins\\example.dll"),
            (r"addons\plugins\example.dll", None),
        )
        self.assertEqual(
            SSHManager._decode_tar_listing_name(r"literal\\n.txt"),
            (r"literal\n.txt", None),
        )
        decoded, error = SSHManager._decode_tar_listing_name(r"line\nbreak.txt")
        self.assertIsNone(error)
        self.assertEqual(decoded, "line\nbreak.txt")

    def test_tar_listing_escape_decoder_reassembles_utf8_octal_bytes(self):
        decoded, error = SSHManager._decode_tar_listing_name(
            r"\345\244\207\344\273\275/\351\205\215\347\275\256.cfg"
        )
        self.assertIsNone(error)
        self.assertEqual(decoded, "备份/配置.cfg")

        decoded, error = SSHManager._decode_tar_listing_name(r"invalid\777.txt")
        self.assertIsNone(decoded)
        self.assertIn("octal", error.lower())

    def test_tar_c_verbose_listing_parser_preserves_quoted_names(self):
        member, error = SSHManager._parse_tar_c_verbose_listing_line(
            r'-rw-r--r-- 0/0 12 2026-07-15 04:00:00 " leading.txt"'
        )
        self.assertIsNone(error)
        self.assertEqual(member, (" leading.txt", False))

        member, error = SSHManager._parse_tar_c_verbose_listing_line(
            r'-rw-r--r-- 0/0 12 2026-07-15 04:00:00 "quote\"name.txt"'
        )
        self.assertIsNone(error)
        self.assertEqual(member, ('quote"name.txt', False))

        member, error = SSHManager._parse_tar_c_verbose_listing_line(
            r'-rw-r--r-- 0/0 12 2026-07-15 04:00:00 "backup\\cfg\\server.cfg"'
        )
        self.assertIsNone(error)
        self.assertEqual(member, (r"backup\cfg\server.cfg", False))

        member, error = SSHManager._parse_tar_c_verbose_listing_line(
            r'lrwxrwxrwx 0/0 0 2026-07-15 04:00:00 "link" -> "../../outside"'
        )
        self.assertIsNone(member)
        self.assertIn("link", error.lower())

    def test_archive_member_normalization_rejects_escape_and_ambiguous_paths(self):
        unsafe_members = (
            "",
            "/",
            "//",
            "/etc/passwd",
            "./C:/Windows/system.ini",
            "../outside",
            "addons/../../outside",
            "addons//plugin.dll",
            "addons/./plugin.dll",
            r"addons\plugin.dll",
            "C:/Windows/system.ini",
            "line\nbreak",
        )
        for member in unsafe_members:
            with self.subTest(member=repr(member)):
                normalized, error = SSHManager._normalize_archive_member(member)
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_archive_info_builds_selectable_folders(self):
        success, info, error = SSHManager._build_archive_info(
            "zip",
            [
                ("addons/", True),
                ("addons/plugins/", True),
                ("addons/plugins/example.dll", False),
                ("cfg/server.cfg", False),
                ("README.md", False),
            ],
        )

        self.assertTrue(success, error)
        self.assertEqual(info["archive_type"], "zip")
        self.assertEqual(info["entry_count"], 5)
        self.assertEqual(info["folders"], ["addons", "cfg", "addons/plugins"])

    def test_tar_archive_info_normalizes_backslashes_and_detects_collisions(self):
        success, info, error = SSHManager._build_archive_info(
            "tar.gz",
            [
                (r"backup\cfg", True),
                (r"backup\cfg\server.cfg", False),
            ],
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["backup", "backup/cfg"])
        self.assertTrue(info["has_backslash_separators"])

        success, _, error = SSHManager._build_archive_info(
            "tar.gz",
            [(r"backup\cfg\server.cfg", False), ("backup/cfg/server.cfg", False)],
        )
        self.assertFalse(success)
        self.assertIn("duplicate", error.lower())

    def test_tar_extract_command_normalizes_backslashes_only_when_needed(self):
        normalized_command = SSHManager._tar_extract_command(
            "/bin/tar",
            "tar.gz",
            "/srv/game/server backup.tar.gz",
            "/srv/game/.stage",
            True,
        )
        self.assertIn(
            r"--transform=flags=rSH;s|\\|/|g",
            shlex.split(normalized_command),
        )
        self.assertIn("TAR_OPTIONS=", shlex.split(normalized_command))
        self.assertIn("/srv/game/server backup.tar.gz", shlex.split(normalized_command))

        regular_command = SSHManager._tar_extract_command(
            "/bin/tar",
            "tar.gz",
            "/srv/game/server.tar.gz",
            "/srv/game/.stage",
            False,
        )
        self.assertNotIn("--transform=", regular_command)

        zstd_command = SSHManager._tar_extract_command(
            "/bin/tar",
            "tar.zst",
            "/srv/game/server.tar.zst",
            "/srv/game/.stage",
            False,
            "/usr/bin/zstd",
        )
        self.assertIn("-I", shlex.split(zstd_command))
        self.assertIn("/usr/bin/zstd", shlex.split(zstd_command))
        self.assertIn("-xf", shlex.split(zstd_command))

    def test_single_file_output_name_strips_compound_and_simple_suffixes(self):
        self.assertEqual(
            SSHManager._single_file_output_name("/srv/game/plugin.so.zst", "zst"),
            "plugin.so",
        )
        self.assertEqual(
            SSHManager._single_file_output_name("/srv/game/plugin.so.zstd", "zst"),
            "plugin.so",
        )
        self.assertEqual(
            SSHManager._single_file_output_name("/srv/game/server.cfg.xz", "xz"),
            "server.cfg",
        )

    def test_archive_info_rejects_duplicate_and_file_ancestor_conflicts(self):
        success, _, error = SSHManager._build_archive_info(
            "tar",
            [("./addons/plugin.dll", False), ("addons/plugin.dll", False)],
        )
        self.assertFalse(success)
        self.assertIn("duplicate", error.lower())

        success, _, error = SSHManager._build_archive_info(
            "tar",
            [("addons", False), ("addons/plugin.dll", False)],
        )
        self.assertFalse(success)
        self.assertIn("ancestor", error.lower())

    def test_archive_info_enforces_entry_limit(self):
        with patch.object(SSHManager, "ARCHIVE_MAX_ENTRIES", 1):
            success, _, error = SSHManager._build_archive_info(
                "zip",
                [("one.txt", False), ("two.txt", False)],
            )
        self.assertFalse(success)
        self.assertIn("too many", error.lower())

    def test_7z_technical_listing_parses_directories_and_files(self):
        listing = """
Path = archive.7z
Type = 7z

----------
Path = addons
Folder = +
Attributes = D drwxr-xr-x

Path = addons/plugin.dll
Folder = -
Attributes = A -rw-r--r--
"""
        members, error = SSHManager._parse_7z_listing(listing)
        self.assertIsNone(error)
        self.assertEqual(
            members,
            [("addons", True), ("addons/plugin.dll", False)],
        )

    def test_7z_technical_listing_rejects_links(self):
        listing = """
----------
Path = addons/link
Folder = -
Symbolic Link = ../../outside
Attributes = A lrwxrwxrwx
"""
        members, error = SSHManager._parse_7z_listing(listing)
        self.assertIsNone(members)
        self.assertIn("link", error.lower())
