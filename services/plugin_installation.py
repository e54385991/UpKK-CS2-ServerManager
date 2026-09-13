"""GitHub plugin installation use case shared by API and schedulers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    Server,
    User,
)
from services.deployment_progress import send_deployment_update
from services.discord_notification_service import (
    EVENT_PLUGIN_UPDATE,
    discord_notification_service,
)
from services.plugins.counterstrikesharp_core import (
    maybe_apply_counterstrikesharp_core_defaults,
)
from services.plugins.github_install.install import install_github_plugin
from services.plugins.install_commands import (
    build_backup_command as _build_backup_command,
)
from services.plugins.install_commands import (
    build_plugin_copy_command as _build_plugin_copy_command,
)
from services.plugins.install_commands import (
    build_rollback_command as _build_rollback_command,
)
from services.plugins.install_commands import (
    remote_plugin_temp_dir as _remote_plugin_temp_dir,
)
from services.plugins.install_retry import (
    PLUGIN_INSTALL_MAX_RETRIES,
    _is_retryable_install_failure,
)
from services.ssh_manager import SSHManager

PROGRESS_UPDATE_INTERVAL = 10

# Source-text contract for tests/test_ai_streaming.py.
_AI_PROGRESS_FORWARD = "ai_progress"


def _archive_cache_scope(request: GitHubPluginInstallRequest) -> str:
    """Readable cache prefix so an operator can identify a file before deleting it.

    Uniqueness comes from the digest the cache appends, not from this label; the
    label only has to say which plugin an archive belongs to.
    """
    repository = (request.repo_url or "").strip().rstrip("/")
    if repository:
        return f"plugin-{'-'.join(repository.split('/')[-2:])}"
    return f"plugin-{(request.display_name or 'archive').strip()}"


async def get_server_for_user(
    db: AsyncSession,
    server_id: int,
    user: User,
) -> Server:
    server = (
        await Server.get_by_id(db, server_id)
        if user.is_admin
        else await Server.get_by_id_and_user(db, server_id, user.id)
    )
    if server is None:
        raise LookupError("Server not found")
    await db.commit()
    return server


async def install_github_plugin_with_retry(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: AsyncSession,
    current_user: User,
    ai_progress: Callable[[str, str], Awaitable[None]] | None = None,
    *,
    max_retries: int = PLUGIN_INSTALL_MAX_RETRIES,
    operation_id: str | None = None,
) -> GitHubPluginInstallResponse:
    """Install a plugin and retry transient or package-layout failures twice."""
    total_attempts = max(1, max_retries + 1)
    failures: list[str] = []
    last_result: GitHubPluginInstallResponse | None = None

    async def report(message: str, message_type: str = "status") -> None:
        if ai_progress is None:
            return
        try:
            await ai_progress(message, message_type)
        except Exception:
            pass

    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            await report(f"Retrying plugin installation (attempt {attempt}/{total_attempts})")
        try:
            result = await install_github_plugin(
                server_id,
                request,
                db,
                current_user,
                ai_progress=ai_progress,
                operation_id=operation_id,
            )
        except LookupError, PermissionError:
            raise
        except Exception as exc:
            result = GitHubPluginInstallResponse(
                success=False,
                message=f"Installation error: {exc}",
            )
        if result.success:
            if attempt > 1:
                await report(
                    f"Plugin installation succeeded on attempt {attempt}/{total_attempts}",
                    "success",
                )
            return result

        last_result = result
        failures.append(result.message)
        if attempt >= total_attempts or not _is_retryable_install_failure(result.message):
            break
        await report(
            f"Plugin installation attempt {attempt}/{total_attempts} failed: "
            f"{result.message}. Retrying automatically.",
            "warning",
        )

    assert last_result is not None
    if len(failures) == 1:
        return last_result
    final_message = (
        f"{last_result.message} (installation failed after {len(failures)} attempts, "
        f"including {len(failures) - 1} automatic retries)"
    )
    await report(final_message, "error")
    return GitHubPluginInstallResponse(
        success=False,
        message=final_message,
        installed_files=last_result.installed_files,
    )


_PATCHABLE = (
    EVENT_PLUGIN_UPDATE,
    PLUGIN_INSTALL_MAX_RETRIES,
    SSHManager,
    _build_backup_command,
    _build_plugin_copy_command,
    _build_rollback_command,
    _is_retryable_install_failure,
    _remote_plugin_temp_dir,
    discord_notification_service,
    maybe_apply_counterstrikesharp_core_defaults,
    send_deployment_update,
)
