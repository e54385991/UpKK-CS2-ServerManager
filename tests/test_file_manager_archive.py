#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
import shlex
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api.routes import file_manager
from services.ssh_manager import SSHManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
            "https://example.com/downloads/latest?id=opaque",
            ("https://github.com/Source2ZE/CS2Fixes/actions/runs/29046667365/artifacts/8210241957"),
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
            "http://2130706433/archive.zip",
            "http://127.1/archive.zip",
            "http://0177.0.0.1/archive.zip",
            "http://0x7f000001/archive.zip",
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

    def test_download_filename_can_be_deferred_for_suffixless_redirect_urls(self):
        suffixless_urls = (
            "https://example.com/download?id=1",
            ("https://github.com/Source2ZE/CS2Fixes/actions/runs/29046667365/artifacts/8210241957"),
        )
        for url in suffixless_urls:
            with self.subTest(url=url):
                self.assertIsNone(
                    file_manager._download_archive_filename(
                        url,
                        None,
                        allow_unresolved=True,
                    )
                )

    def test_github_actions_artifact_url_parser_is_canonical_and_host_bound(self):
        artifact_url = (
            "https://github.com/Source2ZE/CS2Fixes/actions/runs/29046667365/artifacts/8210241957"
        )
        self.assertEqual(
            file_manager._parse_github_actions_artifact_url(artifact_url),
            ("Source2ZE", "CS2Fixes", 8210241957),
        )

        lookalikes = (
            artifact_url.replace("github.com", "github.example.com"),
            artifact_url.replace("/runs/29046667365", ""),
            artifact_url + "/unexpected",
            artifact_url.replace("29046667365", "not-a-run"),
            artifact_url.replace("8210241957", "not-an-artifact"),
        )
        for url in lookalikes:
            with self.subTest(url=url):
                self.assertIsNone(file_manager._parse_github_actions_artifact_url(url))

    def test_github_actions_artifact_resolution_uses_metadata_and_manual_redirect(self):
        artifact_url = (
            "https://github.com/Source2ZE/CS2Fixes/actions/runs/29046667365/artifacts/8210241957"
        )
        api_url = "https://api.github.com/repos/Source2ZE/CS2Fixes/actions/artifacts/8210241957"
        signed_url = "https://objects.githubusercontent.com/artifact.zip?signature=secret"

        class FakeGithubClient:
            def __init__(self):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def get(self, url, headers):
                self.calls.append((url, dict(headers)))
                request = file_manager.httpx.Request("GET", url)
                if url == api_url:
                    return file_manager.httpx.Response(
                        200,
                        json={"name": "CS2Fixes Linux", "expired": False},
                        request=request,
                    )
                if url == f"{api_url}/zip":
                    return file_manager.httpx.Response(
                        302,
                        headers={"Location": signed_url},
                        request=request,
                    )
                raise AssertionError(f"Unexpected request: {url}")

        client = FakeGithubClient()
        with patch.object(
            file_manager.httpx,
            "AsyncClient",
            return_value=client,
        ) as client_factory:
            resolved_url, filename = asyncio.run(
                file_manager._resolve_github_actions_artifact(
                    artifact_url,
                    " github-token ",
                )
            )

        self.assertEqual(resolved_url, signed_url)
        self.assertEqual(filename, "CS2Fixes Linux.zip")
        self.assertEqual([call[0] for call in client.calls], [api_url, f"{api_url}/zip"])
        self.assertTrue(
            all(call[1].get("Authorization") == "Bearer github-token" for call in client.calls)
        )
        self.assertFalse(client_factory.call_args.kwargs["follow_redirects"])

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


class RemoteDownloadSsrfTests(unittest.TestCase):
    def test_remote_url_validation_rejects_nonpublic_literal_hosts(self):
        unsafe_urls = (
            "http://10.0.0.7/archive.zip",
            "http://169.254.169.254/latest/meta-data.zip",
            "http://127.0.0.1/archive.zip",
            "http://[::1]/archive.zip",
            "http://[fe80::1]/archive.zip",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                parsed, error = SSHManager._validate_remote_download_url(url)
                self.assertIsNone(parsed)
                self.assertTrue(error)

    def test_remote_dns_resolution_rejects_nonpublic_and_mixed_answers(self):
        answer_sets = (
            "10.0.0.7 STREAM private.example\n",
            "169.254.169.254 STREAM metadata.example\n",
            "127.0.0.1 STREAM loopback.example\n",
            "::1 STREAM loopback-v6.example\n",
            (
                "8.8.8.8 STREAM mixed.example\n"
                "2606:4700:4700::1111 STREAM mixed.example\n"
                "10.0.0.7 STREAM mixed.example\n"
            ),
        )

        for stdout in answer_sets:
            with self.subTest(stdout=stdout):
                manager = SSHManager(use_pool=False)
                manager.conn = object()

                async def execute(command, timeout=30, stdout=stdout):
                    self.assertIn("getent", command)
                    self.assertIn("ahosts", command)
                    return True, stdout, ""

                manager.execute_command = execute
                address, error = asyncio.run(
                    manager._resolve_public_download_address(
                        "mixed.example",
                        "/usr/bin/getent",
                    )
                )
                self.assertIsNone(address)
                self.assertTrue(error)

    def test_remote_dns_resolution_accepts_only_all_public_answers(self):
        manager = SSHManager(use_pool=False)
        manager.conn = object()

        async def execute(command, timeout=30):
            return (
                True,
                "8.8.8.8 STREAM public.example\n2606:4700:4700::1111 STREAM public.example\n",
                "",
            )

        manager.execute_command = execute
        address, error = asyncio.run(
            manager._resolve_public_download_address(
                "public.example",
                "/usr/bin/getent",
            )
        )
        self.assertEqual(address, "8.8.8.8")
        self.assertEqual(error, "")

    def test_redirect_location_is_joined_then_revalidated(self):
        current_url = "https://downloads.example.com/releases/current/start"
        headers = "HTTP/1.1 302 Found\r\nLocation: ../release.zip?signature=opaque\r\n\r\n"
        next_url, is_redirect, error = SSHManager._redirect_url_from_response(
            headers,
            current_url,
        )
        self.assertTrue(is_redirect)
        self.assertEqual(
            next_url,
            "https://downloads.example.com/releases/release.zip?signature=opaque",
        )
        self.assertEqual(error, "")

    def test_redirect_rejects_private_metadata_and_non_http_schemes(self):
        unsafe_locations = (
            "http://169.254.169.254/latest/meta-data",
            "http://127.0.0.1/archive.zip",
            "ftp://downloads.example.com/archive.zip",
            "file:///etc/passwd",
        )
        for location in unsafe_locations:
            with self.subTest(location=location):
                headers = f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n\r\n"
                next_url, is_redirect, error = SSHManager._redirect_url_from_response(
                    headers,
                    "https://downloads.example.com/start",
                )
                self.assertTrue(is_redirect)
                self.assertIsNone(next_url)
                self.assertTrue(error)


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

    def test_inspection_reports_missing_required_tool_without_running_archive(self):
        manager = self.manager_with_commands(None, [])

        success, info, error = self.run_async(
            manager._inspect_archive_connected("/srv/game/archive.zip", "zip")
        )

        self.assertFalse(success)
        self.assertEqual(info, {})
        self.assertIn("install unzip", error)
        self.assertEqual(manager.commands, [])


class _ArchiveByteStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        if self.chunks:
            return self.chunks.pop(0)
        return b""


class _ArchiveListingProcess:
    def __init__(self, stdout_chunks, stderr_chunks=(), exit_status=0):
        self.stdout = _ArchiveByteStream(stdout_chunks)
        self.stderr = _ArchiveByteStream(stderr_chunks)
        self.exit_status = exit_status
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        return SimpleNamespace(exit_status=self.exit_status)


class _StubbornArchiveListingProcess(_ArchiveListingProcess):
    def __init__(self, stdout_chunks):
        super().__init__(stdout_chunks)
        self._killed = asyncio.Event()

    def kill(self):
        super().kill()
        self._killed.set()

    async def wait(self):
        await self._killed.wait()
        return SimpleNamespace(exit_status=-9)


class _ArchiveListingConnection:
    def __init__(self, process):
        self.process = process
        self.calls = []

    async def create_process(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.process


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


class _TaskSSHManager:
    instances = []
    extraction_result = (True, "")
    download_result = (True, "")
    resolved_target_path = None

    def __init__(self):
        self.disconnected = False
        self.extraction_call = None
        self.download_call = None
        type(self).instances.append(self)

    async def connect(self, server):
        return True, "Connected"

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

    async def download_url_to_file(
        self,
        url,
        target_path,
        server,
        overwrite=False,
        *,
        destination_path=None,
        resolved_target_callback=None,
    ):
        self.download_call = (url, target_path, overwrite, destination_path)
        if type(self).resolved_target_path and resolved_target_callback:
            await resolved_target_callback(type(self).resolved_target_path)
        return type(self).download_result

    async def disconnect(self):
        self.disconnected = True


class FileManagerTaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        _TaskSSHManager.instances = []
        _TaskSSHManager.extraction_result = (True, "")
        _TaskSSHManager.download_result = (True, "")
        _TaskSSHManager.resolved_target_path = None

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
                    "/srv/game",
                    "/srv/game/archive.zip",
                    SimpleNamespace(),
                    False,
                    None,
                )
            )

        manager = _TaskSSHManager.instances[0]
        self.assertEqual(
            manager.download_call,
            (secret_url, "/srv/game/archive.zip", False, "/srv/game"),
        )
        self.assertTrue(manager.disconnected)
        self.assertEqual(file_manager.download_url_tasks[task_id]["status"], "completed")
        self.assertNotIn("url", file_manager.download_url_tasks[task_id])
        self.assertNotIn(task_id, file_manager._download_url_task_refs)

    def test_download_task_updates_target_path_after_redirect_filename_resolution(self):
        task_id = "redirect-download-test"
        url = "https://example.com/download?id=opaque"
        resolved_path = "/srv/game/CS2Fixes Linux.zip"
        _TaskSSHManager.resolved_target_path = resolved_path
        file_manager.download_url_tasks[task_id] = {
            "status": "pending",
            "target_path": None,
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
                    url,
                    "/srv/game",
                    None,
                    SimpleNamespace(),
                    False,
                    None,
                )
            )

        manager = _TaskSSHManager.instances[0]
        self.assertEqual(manager.download_call, (url, None, False, "/srv/game"))
        self.assertEqual(
            file_manager.download_url_tasks[task_id]["target_path"],
            resolved_path,
        )
        self.assertEqual(file_manager.download_url_tasks[task_id]["status"], "completed")
        self.assertTrue(manager.disconnected)

    def test_github_artifact_task_updates_target_and_sends_only_signed_url_to_ssh(self):
        task_id = "github-artifact-download-test"
        artifact_url = (
            "https://github.com/Source2ZE/CS2Fixes/actions/runs/29046667365/artifacts/8210241957"
        )
        signed_url = "https://objects.githubusercontent.com/artifact.zip?signature=secret"
        file_manager.download_url_tasks[task_id] = {
            "status": "pending",
            "target_path": None,
            "created_at": 1.0,
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }
        file_manager._download_url_task_refs[task_id] = object()

        async def resolve_artifact(url, token):
            self.assertEqual(url, artifact_url)
            self.assertEqual(token, "github-token")
            return signed_url, "CS2Fixes Linux.zip"

        with (
            patch.object(file_manager, "SSHManager", _TaskSSHManager),
            patch.object(
                file_manager,
                "_resolve_github_actions_artifact",
                resolve_artifact,
            ),
        ):
            asyncio.run(
                file_manager._run_download_url_task(
                    task_id,
                    artifact_url,
                    "/srv/game",
                    None,
                    SimpleNamespace(),
                    False,
                    "github-token",
                )
            )

        manager = _TaskSSHManager.instances[0]
        self.assertEqual(
            manager.download_call,
            (signed_url, "/srv/game/CS2Fixes Linux.zip", False, "/srv/game"),
        )
        self.assertEqual(
            file_manager.download_url_tasks[task_id]["target_path"],
            "/srv/game/CS2Fixes Linux.zip",
        )
        self.assertEqual(file_manager.download_url_tasks[task_id]["status"], "completed")


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
        template_path, ancestors, (_, modal_attributes) = matches[0]
        self.assertEqual(template_path.name, "files_tab.html")
        self.assertTrue(
            any(
                tag == "template" and attributes.get("x-teleport") == "body"
                for tag, attributes in ancestors
            ),
            "createFolderModal must remain inside an Alpine x-teleport=body template",
        )
        self.assertNotIn(
            "x-show",
            modal_attributes,
            "Bootstrap must be the sole visibility controller for the folder modal",
        )

        scripts = (PROJECT_ROOT / "templates/server_detail_includes/scripts.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "bootstrap.Modal.getOrCreateInstance(modalElement)",
            scripts,
            "The folder modal must reuse one Bootstrap instance instead of flickering",
        )


if __name__ == "__main__":
    unittest.main()
