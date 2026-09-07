"""Reuse, naming and retention for the panel's plugin/framework archive cache."""

import hashlib
import os
import time

import pytest

from modules.models import SystemSettings
from services import plugin_download_cache as cache
from services.plugins import download_reuse


def write(path, data: bytes = b"archive-bytes"):
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


def test_default_root_is_the_program_data_directory():
    assert cache.DEFAULT_CACHE_PATH.name == "cache_serverplugins"
    assert cache.DEFAULT_CACHE_PATH.parent.name == "data"


def test_same_filename_from_two_plugins_never_collides(tmp_path):
    root = str(tmp_path)
    first = cache.cached_path(
        root, "https://github.com/a/one/releases/download/v1/plugin.zip", scope="plugin-a-one"
    )
    second = cache.cached_path(
        root, "https://github.com/b/two/releases/download/v1/plugin.zip", scope="plugin-b-two"
    )
    assert first != second
    # The readable prefix is for an operator browsing the directory; the digest
    # is what actually guarantees uniqueness.
    assert first.name.startswith("plugin-a-one-") and first.name.endswith("plugin.zip")


def test_versions_of_one_url_are_separate_entries(tmp_path):
    url = "https://example.test/plugin.zip"
    assert cache.cached_path(str(tmp_path), url, "v1") != cache.cached_path(
        str(tmp_path), url, "v2"
    )


def test_scope_is_reduced_to_a_safe_prefix():
    assert cache.safe_scope("plugin/../../etc") == "plugin-etc"
    assert cache.safe_scope("  ") == "download"
    assert "/" not in cache.safe_scope("a/b") and len(cache.safe_scope("x" * 200)) <= 60


def test_put_then_get_reuses_the_stored_archive(tmp_path):
    source = write(tmp_path / "source.zip")
    url = "https://example.test/plugin.zip"
    stored = cache.put(str(tmp_path / "cache"), source, url, scope="plugin-demo")
    assert stored.read_bytes() == b"archive-bytes"
    assert cache.get(str(tmp_path / "cache"), url, scope="plugin-demo") == stored
    assert cache.get(str(tmp_path / "cache"), url, scope="other") is None


def test_get_refreshes_last_use_so_retention_is_least_recently_used(tmp_path):
    root = str(tmp_path / "cache")
    url = "https://example.test/plugin.zip"
    stored = cache.put(root, write(tmp_path / "source.zip"), url, scope="plugin-demo")
    os.utime(stored, (0, 0))
    cache.get(root, url, scope="plugin-demo")
    assert time.time() - stored.stat().st_mtime < 60


def test_prune_drops_expired_entries_and_leaves_fresh_ones(tmp_path):
    root = str(tmp_path / "cache")
    old = cache.put(root, write(tmp_path / "a.zip"), "https://example.test/a.zip", scope="a")
    fresh = cache.put(root, write(tmp_path / "b.zip"), "https://example.test/b.zip", scope="b")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    removed, freed = cache.prune(cache.CachePolicy(path=root, max_age_days=30, max_megabytes=0))
    assert removed == 1 and freed == len(b"archive-bytes")
    assert not old.exists() and fresh.exists()


def test_prune_enforces_the_size_ceiling_oldest_first(tmp_path):
    root = str(tmp_path / "cache")
    entries = []
    for index in range(3):
        stored = cache.put(
            root,
            write(tmp_path / f"{index}.zip", b"x" * 700_000),
            f"https://example.test/{index}.zip",
            scope=f"plugin-{index}",
        )
        os.utime(stored, (time.time() - (10 - index) * 3600,) * 2)
        entries.append(stored)
    removed, _freed = cache.prune(cache.CachePolicy(path=root, max_age_days=0, max_megabytes=2))
    assert removed == 1
    assert not entries[0].exists() and entries[1].exists() and entries[2].exists()


def test_zero_limits_switch_retention_off(tmp_path):
    root = str(tmp_path / "cache")
    stored = cache.put(root, write(tmp_path / "a.zip"), "https://example.test/a.zip", scope="a")
    os.utime(stored, (0, 0))
    assert cache.prune(cache.CachePolicy(path=root, max_age_days=0, max_megabytes=0)) == (0, 0)
    assert stored.exists()


def test_stats_and_clear_ignore_partial_downloads(tmp_path):
    root = str(tmp_path / "cache")
    cache.put(root, write(tmp_path / "a.zip"), "https://example.test/a.zip", scope="a")
    write(cache.cache_root(root) / f"leftover{cache.TEMP_SUFFIX}")
    stats = cache.stats(root)
    assert stats["files"] == 1 and stats["bytes"] == len(b"archive-bytes")
    assert cache.clear(root) == 2 and cache.stats(root)["files"] == 0


def test_policy_reads_saved_settings_and_clamps_out_of_range_values():
    settings = SystemSettings(
        plugin_download_cache_enabled=False,
        plugin_download_cache_path="/tmp/x",
        plugin_download_cache_max_age_days=-5,
        plugin_download_cache_max_megabytes=10_000_000,
    )
    policy = cache.CachePolicy.from_settings(settings)
    assert policy.enabled is False and policy.path == "/tmp/x"
    assert policy.max_age_days == 0
    assert policy.max_megabytes == cache.MAX_MEGABYTES_LIMIT


@pytest.mark.asyncio
async def test_cached_download_serves_the_second_request_without_the_network(tmp_path, monkeypatch):
    calls = []

    async def fake_download(url, local_path, **_kwargs):
        calls.append(url)
        write(local_path)
        return True, None

    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", fake_download)
    policy = cache.CachePolicy(path=str(tmp_path / "cache"))
    url = "https://example.test/plugin.zip"

    first = str(tmp_path / "first.zip")
    assert await download_reuse.cached_download(url, first, scope="plugin-demo", policy=policy) == (
        True,
        None,
    )
    second = str(tmp_path / "second.zip")
    assert await download_reuse.cached_download(
        url, second, scope="plugin-demo", policy=policy
    ) == (True, None)
    assert calls == [url]
    assert open(second, "rb").read() == b"archive-bytes"


@pytest.mark.asyncio
async def test_a_proxied_url_reuses_the_canonical_entry(tmp_path, monkeypatch):
    """Switching GitHub proxies must not invalidate everything already on disk."""
    calls = []

    async def fake_download(url, local_path, **_kwargs):
        calls.append(url)
        write(local_path)
        return True, None

    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", fake_download)
    policy = cache.CachePolicy(path=str(tmp_path / "cache"))
    canonical = "https://github.com/a/b/releases/download/v1/plugin.zip"
    await download_reuse.cached_download(
        f"https://proxy.test/{canonical}",
        str(tmp_path / "one.zip"),
        scope="plugin-a-b",
        cache_url=canonical,
        policy=policy,
    )
    await download_reuse.cached_download(
        f"https://other-proxy.test/{canonical}",
        str(tmp_path / "two.zip"),
        scope="plugin-a-b",
        cache_url=canonical,
        policy=policy,
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_disabled_cache_always_downloads(tmp_path, monkeypatch):
    calls = []

    async def fake_download(url, local_path, **_kwargs):
        calls.append(url)
        write(local_path)
        return True, None

    from modules.http_helper import http_helper

    monkeypatch.setattr(http_helper, "download_file", fake_download)
    for target in ("one.zip", "two.zip"):
        await download_reuse.cached_download(
            "https://example.test/plugin.zip",
            str(tmp_path / target),
            scope="plugin-demo",
            policy=cache.DISABLED,
        )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_release_asset_reuse_verifies_the_approved_digest(tmp_path, monkeypatch):
    downloads = []

    payload = b"archive-bytes"
    real_digest = hashlib.sha256(payload).hexdigest()

    async def fake_asset(url):
        downloads.append(url)
        return write(tmp_path / f"download-{len(downloads)}.zip", payload), real_digest, 13

    monkeypatch.setattr(
        "services.plugins.github_assets.download_release_asset", fake_asset, raising=True
    )
    policy = cache.CachePolicy(path=str(tmp_path / "cache"))
    url = "https://github.com/a/b/releases/download/v1/plugin.zip"

    path, digest, size = await download_reuse.cached_release_asset(
        url, scope="plugin-a-b", policy=policy
    )
    os.unlink(path)
    assert downloads == [url] and size == 13

    # A hit returns a fresh temp copy the caller still owns and may delete.
    reused, reused_digest, reused_size = await download_reuse.cached_release_asset(
        url, scope="plugin-a-b", expected_sha256=digest, policy=policy
    )
    assert downloads == [url] and reused_digest == digest and reused_size == 13
    assert cache.get(policy.path, url, scope="plugin-a-b") is not None
    os.unlink(reused)

    # A cached entry that no longer matches the approved plan is re-downloaded.
    stale, _digest, _size = await download_reuse.cached_release_asset(
        url, scope="plugin-a-b", expected_sha256="b" * 64, policy=policy
    )
    os.unlink(stale)
    assert downloads == [url, url]


@pytest.mark.asyncio
async def test_an_unreadable_policy_degrades_to_downloading(monkeypatch):
    def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(download_reuse, "async_session_maker", broken)
    assert await download_reuse.current_policy() is cache.DISABLED
