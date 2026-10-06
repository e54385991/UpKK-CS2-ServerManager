"""Exercise native runtime dependencies on each supported Python version."""

from __future__ import annotations

import asyncio
import sys

import hiredis
import httptools
import py7zr
import pytest
from py7zr.properties import FILTER_DEFLATE64


def test_native_redis_parser_reads_responses():
    reader = hiredis.Reader()
    reader.feed(b"*2\r\n$4\r\nPONG\r\n:42\r\n")
    assert reader.gets() == [b"PONG", 42]
    assert reader.gets() is False


def test_native_http_parser_reads_requests():
    urls = []

    class Request:
        def on_url(self, url):
            urls.append(url)

    parser = httptools.HttpRequestParser(Request())
    parser.feed_data(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert urls == [b"/health"]
    assert parser.get_method() == b"GET"


@pytest.mark.parametrize(
    "filters",
    [
        [{"id": py7zr.FILTER_LZMA2}],
        [{"id": py7zr.FILTER_PPMD}],
        [{"id": FILTER_DEFLATE64}],
        [{"id": py7zr.FILTER_BROTLI}],
        [{"id": py7zr.FILTER_X86}, {"id": py7zr.FILTER_LZMA2}],
    ],
    ids=["lzma2", "ppmd", "deflate64", "brotli", "bcj"],
)
def test_plugin_archive_codecs_round_trip(tmp_path, filters):
    from services.plugins.release_archive import _seven_entries

    payload = "插件配置\n".encode("utf-8") + bytes(range(256)) * 4
    archive_path = tmp_path / "plugin.7z"
    with py7zr.SevenZipFile(archive_path, mode="w", filters=filters) as archive:
        archive.writestr(payload, "plugin.bin")
    entries = _seven_entries(str(archive_path))
    assert [(item["path"], item["size"]) for item in entries] == [("plugin.bin", len(payload))]

    destination = tmp_path / "extracted"
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extractall(path=destination)
    assert (destination / "plugin.bin").read_bytes() == payload


def test_uvloop_runs_asyncio_tasks():
    if sys.platform in {"win32", "cygwin"} or sys.implementation.name == "pypy":
        pytest.skip("uvloop is excluded from the dependency graph on this platform")
    import uvloop

    async def value():
        await asyncio.sleep(0)
        return 42

    async def probe():
        loop = asyncio.get_running_loop()
        if sys.version_info >= (3, 15):
            task = loop.create_task(value(), eager_start=True)
        else:
            task = loop.create_task(value())
        return await task

    with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
        assert runner.run(probe()) == 42
