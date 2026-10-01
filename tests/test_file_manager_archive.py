#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.routes import file_manager
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
