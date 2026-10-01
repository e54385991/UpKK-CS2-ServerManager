#!/usr/bin/env python3
"""Focused regression tests for file-manager URL downloads and archives.

The suite intentionally uses only ``unittest`` and test doubles. It does not
need a database, Redis, a live SSH server, or a browser runtime.
"""

import asyncio
import unittest
from types import SimpleNamespace

if __name__ == "__main__":
    unittest.main()


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
