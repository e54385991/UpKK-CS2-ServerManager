"""Lifecycle for the panel monitor collector, sampler, and config poll."""

from __future__ import annotations

import asyncio
import logging
from time import time

from modules.observability import (
    clear_all,
    drain_log_queue,
    install_log_handler,
    is_enabled,
    set_enabled,
    set_sync_error,
    uninstall_log_handler,
)
from services.panel_metrics import metrics_store, start_loop_sampler, stop_loop_sampler
from services.panel_monitor.collector import BUCKET_SECONDS, collect_once, record_collect_failure
from services.panel_monitor.history import history
from services.panel_monitor.settings import read_monitoring_enabled
from services.task_registry import BackgroundTaskRegistry

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
STALE_SECONDS = 30

monitor_task_registry = BackgroundTaskRegistry("panel monitor")

_lock = asyncio.Lock()
_workers_running = False
_poll_started = False
_collect_generation = 0
_collect_task: asyncio.Task | None = None


async def start_panel_monitor() -> None:
    """Read the persisted switch, then start workers only when it is on."""
    enabled = await read_monitoring_enabled()
    if enabled is None:
        set_sync_error("config_read_failed")
        enabled = False
    else:
        set_sync_error(None)
    await set_monitoring_enabled(enabled)
    _ensure_poll_loop()


async def stop_panel_monitor() -> None:
    await set_monitoring_enabled(False)
    await monitor_task_registry.shutdown()
    global _poll_started, _workers_running, _collect_task
    _poll_started = False
    _workers_running = False
    _collect_task = None


async def set_monitoring_enabled(enabled: bool) -> None:
    """Apply the in-memory switch and start or stop collection workers."""
    async with _lock:
        set_enabled(enabled)
        if enabled:
            metrics_store.clear()
            await _start_workers_unlocked()
            return
        await _stop_workers_unlocked()
        drain_log_queue()
        clear_all()
        history.clear_buffers()


def _ensure_poll_loop() -> None:
    global _poll_started
    if _poll_started:
        return
    monitor_task_registry.create(_poll_loop())
    _poll_started = True


async def _start_workers_unlocked() -> None:
    global _workers_running, _collect_generation, _collect_task
    if _workers_running:
        return
    install_log_handler()
    await start_loop_sampler()
    _collect_generation += 1
    token = _collect_generation
    _collect_task = monitor_task_registry.create(_collect_loop(token))
    _workers_running = True


async def _stop_workers_unlocked() -> None:
    global _workers_running, _collect_generation, _collect_task
    _collect_generation += 1
    task = _collect_task
    _collect_task = None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await stop_loop_sampler()
    uninstall_log_handler()
    _workers_running = False


async def _collect_loop(token: int) -> None:
    while is_enabled() and token == _collect_generation:
        await _wait_for_bucket()
        if not is_enabled() or token != _collect_generation:
            return
        try:
            await collect_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            record_collect_failure()


async def _wait_for_bucket() -> None:
    now = time()
    delay = max(0.05, (int(now // BUCKET_SECONDS) + 1) * BUCKET_SECONDS - now)
    await asyncio.sleep(delay)


async def _poll_loop() -> None:
    while True:
        await asyncio.sleep(POLL_SECONDS)
        enabled = await read_monitoring_enabled()
        if enabled is None:
            set_sync_error("config_read_failed")
            continue
        set_sync_error(None)
        if enabled != is_enabled():
            await set_monitoring_enabled(enabled)


def sample_is_stale(now: float | None = None) -> bool:
    last = history.last_sample_at
    if last is None:
        return is_enabled()
    return (now or time()) - last > STALE_SECONDS
