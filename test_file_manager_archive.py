#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
from html.parser import HTMLParser
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.routes import file_manager
from services.ssh_manager import SSHManager


PROJECT_ROOT = Path(__file__).resolve().parent


class FileManagerValidationTests(unittest.TestCase):
    def assert_http_error(self, status_code, callback, *args):
        with self.assertRaises(HTTPException) as caught:
            callback(*args)
        self.assertEqual(caught.exception.status_code, status_code)
        return caught.exception

    def test_direct_child_name_accepts_one_utf8_path_component(self):
        self.assertEqual(
            file_manager._validate_direct_child_name("server files", "Directory name"),
            "server files",
        )
        # The limit is measured in encoded bytes, not Python characters.
        self.assertEqual(
            file_manager._validate_direct_child_name("你" * 85),
            "你" * 85,
        )

    def test_direct_child_name_rejects_empty_traversal_separators_and_controls(self):
        invalid_names = (
            "",
            "   ",
            ".",
            "..",
            "../plugins",
            "addons/plugins",
            r"addons\plugins",
            "line\nbreak",
            "nul\x00byte",
            "你" * 86,
        )
        for name in invalid_names:
            with self.subTest(name=repr(name)):
                self.assert_http_error(
                    422,
                    file_manager._validate_direct_child_name,
                    name,
                    "Directory name",
                )

    def test_source_folder_normalization(self):
        self.assertIsNone(file_manager._normalize_source_folder(None))
        self.assertIsNone(file_manager._normalize_source_folder("  "))
        self.assertEqual(
            file_manager._normalize_source_folder(" ./addons/plugins/ "),
            "addons/plugins",
        )
        self.assertEqual(
            file_manager._normalize_source_folder("addons/./plugins"),
            "addons/plugins",
        )

    def test_source_folder_rejects_escape_absolute_backslash_and_controls(self):
        invalid_folders = (
            ".",
            "./",
            "..",
            "../addons",
            "addons/../../etc",
            "/addons",
            r"addons\plugins",
            "addons\nplugins",
        )
        for folder in invalid_folders:
            with self.subTest(folder=repr(folder)):
                self.assert_http_error(
                    422,
                    file_manager._normalize_source_folder,
                    folder,
                )

    def test_download_url_accepts_absolute_public_http_urls(self):
        valid_urls = (
            "https://example.com/releases/server.tar.gz?token=opaque",
            "http://8.8.8.8/archive.zip",
            "https://[2606:4700:4700::1111]/archive.7z",
        )
        for url in valid_urls:
            with self.subTest(url=url):
                self.assertEqual(file_manager._validate_download_url(url), url)

    def test_download_url_rejects_unsafe_schemes_authorities_and_fragments(self):
        invalid_urls = (
            "",
            "/relative/archive.zip",
            "ftp://example.com/archive.zip",
            "file:///etc/passwd",
            "https:///archive.zip",
            "https://user:secret@example.com/archive.zip",
            "https://example.com/archive.zip#fragment",
            "https://example.com:99999/archive.zip",
            "https://example.com/archive.zip\nnext",
        )
        for url in invalid_urls:
            with self.subTest(url=repr(url)):
                self.assert_http_error(422, file_manager._validate_download_url, url)

    def test_download_url_rejects_local_and_non_public_literal_addresses(self):
        invalid_urls = (
            "http://localhost/archive.zip",
            "http://build.localhost/archive.zip",
            "http://127.0.0.1/archive.zip",
            "http://10.0.0.1/archive.zip",
            "http://169.254.169.254/latest/meta-data.zip",
            "http://0.0.0.0/archive.zip",
            "http://[::1]/archive.zip",
            "http://[fe80::1]/archive.zip",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assert_http_error(422, file_manager._validate_download_url, url)

    def test_download_filename_is_explicit_or_derived_from_url_path(self):
        self.assertEqual(
            file_manager._download_archive_filename(
                "https://example.com/download?id=1",
                "release build.TAR.XZ",
            ),
            "release build.TAR.XZ",
        )
        self.assertEqual(
            file_manager._download_archive_filename(
                "https://example.com/releases/release%20build.tar.gz?signature=1",
                None,
            ),
            "release build.tar.gz",
        )

    def test_download_filename_rejects_missing_unsafe_and_unsupported_names(self):
        invalid_cases = (
            ("https://example.com/", None),
            ("https://example.com/archive.zip", "../archive.zip"),
            ("https://example.com/archive.zip", "nested/archive.zip"),
            ("https://example.com/archive.zip", "bad\narchive.zip"),
            ("https://example.com/archive.zip", "archive.exe"),
            ("https://example.com/releases/%2E%2E%2Farchive.zip", None),
        )
        for url, filename in invalid_cases:
            with self.subTest(url=url, filename=filename):
                self.assert_http_error(
                    422,
                    file_manager._download_archive_filename,
                    url,
                    filename,
                )


class ArchivePureHelperTests(unittest.TestCase):
    def test_archive_type_mapping_prefers_compound_suffixes_and_ignores_case(self):
        cases = {
            "bundle.zip": "zip",
            "bundle.7Z": "7z",
            "bundle.tar": "tar",
            "bundle.TAR.GZ": "tar.gz",
            "bundle.tgz": "tar.gz",
            "bundle.tar.bz2": "tar.bz2",
            "bundle.tbz2": "tar.bz2",
            "bundle.tar.xz": "tar.xz",
            "bundle.txz": "tar.xz",
            "bundle.gz": "gz",
            "bundle.bz2": "bz2",
            "bundle.rar": None,
            "bundle.zip.txt": None,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(SSHManager.archive_type_from_path(path), expected)

    def test_archive_member_normalization_accepts_safe_posix_members(self):
        self.assertEqual(
            SSHManager._normalize_archive_member("./addons/plugins/example.dll"),
            ("addons/plugins/example.dll", None),
        )
        self.assertEqual(
            SSHManager._normalize_archive_member("addons/plugins/"),
            ("addons/plugins", None),
        )
        self.assertEqual(SSHManager._normalize_archive_member("./"), (None, None))

    def test_archive_member_normalization_rejects_escape_and_ambiguous_paths(self):
        unsafe_members = (
            "/etc/passwd",
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


class ArchiveRemoteCommandTests(unittest.TestCase):
    @staticmethod
    def run(coroutine):
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

        manager._find_remote_tool = find_tool
        manager.execute_command = execute
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
        result = self.run(manager._find_remote_tool(("7zz", "7z", "7za")))

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

        success, info, error = self.run(
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
                (True, "addons/\naddons/plugin.dll\n", ""),
                (True, "drwxr-xr-x user/group 0 addons/\n-rw-r--r-- user/group 1 addons/plugin.dll\n", ""),
            ],
        )

        success, _, error = self.run(
            manager._inspect_archive_connected("/srv/game/archive.tar.xz", "tar.xz")
        )

        self.assertTrue(success, error)
        self.assertEqual(manager.tool_candidates, [("tar",)])
        self.assertIn(" -tJf ", manager.commands[0][0])
        self.assertIn(" -tvJf ", manager.commands[1][0])

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

        success, info, error = self.run(
            manager._inspect_archive_connected("/srv/game/archive.7z", "7z")
        )

        self.assertTrue(success, error)
        self.assertEqual(info["folders"], ["addons"])
        self.assertEqual(manager.tool_candidates, [("7zz", "7z", "7za")])
        self.assertIn(" l -slt -sccUTF-8 ", manager.commands[0][0])

    def test_inspection_reports_missing_required_tool_without_running_archive(self):
        manager = self.manager_with_commands(None, [])

        success, info, error = self.run(
            manager._inspect_archive_connected("/srv/game/archive.zip", "zip")
        )

        self.assertFalse(success)
        self.assertEqual(info, {})
        self.assertIn("install unzip", error)
        self.assertEqual(manager.commands, [])


class _TaskSSHManager:
    instances = []
    extraction_result = (True, "")
    download_result = (True, "")

    def __init__(self):
        self.disconnected = False
        self.extraction_call = None
        self.download_call = None
        type(self).instances.append(self)

    async def extract_archive(
        self,
        archive_path,
        destination_path,
        server,
        overwrite,
        source_folder=None,
        strip_source_folder=False,
    ):
        self.extraction_call = (
            archive_path,
            destination_path,
            overwrite,
            source_folder,
            strip_source_folder,
        )
        return type(self).extraction_result

    async def download_url_to_file(self, url, target_path, server, overwrite=False):
        self.download_call = (url, target_path, overwrite)
        return type(self).download_result

    async def disconnect(self):
        self.disconnected = True


class FileManagerTaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        _TaskSSHManager.instances = []
        _TaskSSHManager.extraction_result = (True, "")
        _TaskSSHManager.download_result = (True, "")

    def tearDown(self):
        file_manager.extraction_tasks.clear()
        file_manager._extraction_task_refs.clear()
        file_manager.download_url_tasks.clear()
        file_manager._download_url_task_refs.clear()

    def test_extraction_task_forwards_folder_options_and_disconnects(self):
        task_id = "extract-test"
        file_manager.extraction_tasks[task_id] = {
            "status": "pending",
            "created_at": 1.0,
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }
        file_manager._extraction_task_refs[task_id] = object()

        with patch.object(file_manager, "SSHManager", _TaskSSHManager):
            asyncio.run(
                file_manager._run_extraction_task(
                    task_id,
                    "/srv/game/archive.zip",
                    "/srv/game/output",
                    SimpleNamespace(),
                    True,
                    "addons/plugins",
                    True,
                )
            )

        manager = _TaskSSHManager.instances[0]
        self.assertEqual(
            manager.extraction_call,
            (
                "/srv/game/archive.zip",
                "/srv/game/output",
                True,
                "addons/plugins",
                True,
            ),
        )
        self.assertTrue(manager.disconnected)
        self.assertEqual(file_manager.extraction_tasks[task_id]["status"], "completed")
        self.assertNotIn(task_id, file_manager._extraction_task_refs)

    def test_download_task_does_not_retain_url_and_disconnects(self):
        task_id = "download-test"
        secret_url = "https://example.com/archive.zip?signature=secret"
        file_manager.download_url_tasks[task_id] = {
            "status": "pending",
            "target_path": "/srv/game/archive.zip",
            "created_at": 1.0,
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }
        file_manager._download_url_task_refs[task_id] = object()

        with patch.object(file_manager, "SSHManager", _TaskSSHManager):
            asyncio.run(
                file_manager._run_download_url_task(
                    task_id,
                    secret_url,
                    "/srv/game/archive.zip",
                    SimpleNamespace(),
                    False,
                )
            )

        manager = _TaskSSHManager.instances[0]
        self.assertEqual(
            manager.download_call,
            (secret_url, "/srv/game/archive.zip", False),
        )
        self.assertTrue(manager.disconnected)
        self.assertEqual(file_manager.download_url_tasks[task_id]["status"], "completed")
        self.assertNotIn("url", file_manager.download_url_tasks[task_id])
        self.assertNotIn(task_id, file_manager._download_url_task_refs)


class _TemplateIdParser(HTMLParser):
    VOID_ELEMENTS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element = (tag, attributes)
        element_id = attributes.get("id")
        if element_id:
            self.ids.append((element_id, tuple(self.stack), element))
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class FileManagerDomRegressionTests(unittest.TestCase):
    def test_create_folder_modal_is_unique_and_teleported_to_body(self):
        matches = []
        for template_path in (PROJECT_ROOT / "templates").rglob("*.html"):
            parser = _TemplateIdParser()
            parser.feed(template_path.read_text(encoding="utf-8"))
            for element_id, ancestors, element in parser.ids:
                if element_id == "createFolderModal":
                    matches.append((template_path, ancestors, element))

        self.assertEqual(
            len(matches),
            1,
            "createFolderModal must have exactly one template definition",
        )
        template_path, ancestors, _ = matches[0]
        self.assertEqual(template_path.name, "files_tab.html")
        self.assertTrue(
            any(
                tag == "template" and attributes.get("x-teleport") == "body"
                for tag, attributes in ancestors
            ),
            "createFolderModal must remain inside an Alpine x-teleport=body template",
        )


if __name__ == "__main__":
    unittest.main()
