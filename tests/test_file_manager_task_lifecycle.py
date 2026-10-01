#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.routes import file_manager

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
