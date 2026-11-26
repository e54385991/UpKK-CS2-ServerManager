"""Auto-update mixins looked up through the public service facade."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Dict, List, Optional, Tuple

from modules.models import ManagedPlugin, Server, User
from services.compat import LateBoundModule
from services.plugins.ai_install_policy import managed_asset_candidates
from services.plugins.client_cvar_value import matches_legacy_linux_asset
from services.plugins.github_assets import GitHubPlanError
from services.plugins.market_release_update import resolve_market_update
from services.plugins.release_selection import MarketReleaseSync, fetch_release, market_release_sync
from services.plugins.tracking import canonical_repo_url, repo_api_url

logger = logging.getLogger("services.plugin_auto_update_service")
host = LateBoundModule("services.plugin_auto_update_service")


def _select_update_runtime_assets(
    item: ManagedPlugin,
    assets: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    fallback_matches: List[Dict[str, Any]],
    pattern: str,
    linux_runtime_profile: Dict[str, Any],
) -> tuple[bool, List[Dict[str, Any]], str]:
    from services.linux_runtime_service import (
        RuntimeSelectionRequired,
        paired_runtime_families,
        prioritize_runtime_assets,
        steam_runtime_asset_family,
    )

    paired = paired_runtime_families(assets)
    reference_families = {
        family
        for name in [
            item.installed_asset_name,
            *(str(asset.get("name") or "") for asset in matches),
            *(str(asset.get("name") or "") for asset in fallback_matches),
        ]
        if name and (family := steam_runtime_asset_family(str(name)))
    }
    related = paired & reference_families
    if len(related) == 1:
        family = next(iter(related))
        siblings = [
            asset
            for asset in assets
            if steam_runtime_asset_family(str(asset.get("name") or "")) == family
        ]
        try:
            matches = [prioritize_runtime_assets(siblings, linux_runtime_profile)[0]]
        except RuntimeSelectionRequired as exc:
            return False, [], f"{item.display_name}: {exc}"
    elif len(related) > 1:
        return (
            False,
            [],
            f"Multiple paired Steam Runtime asset families match '{pattern}'",
        )
    elif not matches and fallback_matches:
        matches = fallback_matches

    return True, matches, ""


class AutoUpdateAssetsMixin:
    @staticmethod
    def _is_windows_asset(asset_name: str) -> bool:
        """Match the platform filtering used by the plugin-market installer."""
        from services.plugins.market_assets import is_windows_release_asset

        return is_windows_release_asset(asset_name or "")

    @staticmethod
    def _archive_extension(asset_name: str) -> Optional[str]:
        name = (asset_name or "").casefold()
        for extension in host.ARCHIVE_EXTENSIONS:
            if name.endswith(extension):
                return extension
        return None

    @classmethod
    def _fallback_release_assets(
        cls, item: ManagedPlugin, assets: List[Dict[str, Any]], pattern: str
    ) -> List[Dict[str, Any]]:
        """Select a changed-but-compatible market asset without guessing.

        A market record stores a glob derived from the asset selected during
        installation. Projects occasionally remove the version component or
        rename the archive in a later release, which makes the literal glob
        return zero matches. We first retain the fixed prefix/suffix and archive
        type, then (for market records) use the same single Linux archive rule as
        the installer. Every fallback still requires exactly one candidate.
        """
        wildcard_positions = [index for index, char in enumerate(pattern) if char in "*?"]
        if wildcard_positions:
            first_wildcard = wildcard_positions[0]
            last_wildcard = wildcard_positions[-1]
            prefix = pattern[:first_wildcard].casefold()
            suffix = pattern[last_wildcard + 1 :].casefold()
        else:
            prefix = pattern.casefold()
            suffix = ""
        expected_extension = cls._archive_extension(pattern)

        all_archives: List[Dict[str, Any]] = []
        archives: List[Dict[str, Any]] = []
        for asset in assets:
            name = str(asset.get("name") or "")
            if not name or cls._is_windows_asset(name):
                continue
            extension = cls._archive_extension(name)
            if extension is None:
                continue
            all_archives.append(asset)
            if not expected_extension or extension == expected_extension:
                archives.append(asset)

        def has_shape(asset: Dict[str, Any], include_suffix: bool = True) -> bool:
            name = str(asset.get("name") or "").casefold()
            if prefix and not name.startswith(prefix):
                return False
            return not (include_suffix and suffix and not name.endswith(suffix))

        # Keep the stored resource rule authoritative wherever possible. This
        # handles e.g. `Plugin-*-linux.tar.gz` -> `Plugin-linux.tar.gz`.
        shaped = [asset for asset in archives if has_shape(asset)]
        if shaped:
            return shaped

        # A market install historically selected the first suitable Linux
        # archive. For auto-update, only accept this compatibility fallback when
        # it is unambiguous, so a changed naming scheme cannot overwrite a wrong
        # file. GitHub registrations still require their configured prefix.
        if item.source_type == "market" or item.market_plugin_id is not None:
            prefixed = [asset for asset in archives if has_shape(asset, include_suffix=False)]
            if prefixed:
                return prefixed
            if archives:
                return archives
            # The market installer accepts any supported Linux archive. If a
            # project changes tar.gz to zip while retaining the plugin prefix,
            # preserve that behavior, but still require a single candidate at
            # the caller so an ambiguous release is never overwritten.
            return [
                asset for asset in all_archives if has_shape(asset, include_suffix=False)
            ] or all_archives
        return []

    async def _latest_github_release(
        self,
        item: ManagedPlugin,
        user: User,
        linux_runtime_profile: Optional[Dict[str, Any]] = None,
        *,
        pinned: MarketReleaseSync | None = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not item.repo_url:
            return False, None, "GitHub repository is not configured"
        token = user.github_token if user.has_github_token else None
        from services.plugins.ai_install_policy import managed_market_plugin

        market = await managed_market_plugin(item.market_plugin_id)
        if market is not None:
            if (
                canonical_repo_url(market.github_url).casefold()
                != canonical_repo_url(item.repo_url).casefold()
            ):
                return False, None, "Marketplace repository no longer matches the installed plugin"
            from services.github_credentials import get_effective_github_token

            async with host.async_session_maker() as db:
                token = await get_effective_github_token(db, user)
        if pinned is not None:
            if market is None or market_release_sync(market) != pinned:
                return False, None, "Marketplace release changed; review the synchronized release"
            try:
                data = await fetch_release(item.repo_url, token, release_id=pinned.release_id)
                return (
                    True,
                    await resolve_market_update(
                        market, item, data, token, linux_runtime_profile, pinned=pinned
                    ),
                    "",
                )
            except (GitHubPlanError, ValueError) as exc:
                return False, None, str(exc)
        success, data, error = await host.http_helper.get(
            repo_api_url(item.repo_url),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
            timeout=30,
            github_token=token,
        )
        if not success or not isinstance(data, dict):
            return False, None, error or "Failed to fetch latest stable release"
        if data.get("draft") or data.get("prerelease"):
            return False, None, "GitHub latest release is not a stable release"
        if market is not None:
            try:
                return (
                    True,
                    await resolve_market_update(market, item, data, token, linux_runtime_profile),
                    "",
                )
            except (GitHubPlanError, ValueError) as exc:
                return False, None, str(exc)
        return await self._resolve_registered_asset(item, data, linux_runtime_profile)

    async def _resolve_registered_asset(
        self,
        item: ManagedPlugin,
        data: dict[str, Any],
        linux_runtime_profile: dict[str, Any] | None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        assets = data.get("assets") or []
        assets, pattern = await managed_asset_candidates(item, assets)
        matches = [
            asset for asset in assets if fnmatch.fnmatchcase(str(asset.get("name") or ""), pattern)
        ]
        if not matches:
            matches = [
                asset
                for asset in assets
                if matches_legacy_linux_asset(item.repo_url, pattern, str(asset.get("name") or ""))
            ]
        fallback_matches: List[Dict[str, Any]] = []
        if not matches:
            fallback_matches = self._fallback_release_assets(item, assets, pattern)
            if len(fallback_matches) == 1:
                matches = fallback_matches
                logger.info(
                    "Using compatible release asset fallback for %s: glob %r -> %s",
                    item.display_name,
                    pattern,
                    fallback_matches[0].get("name"),
                )
            elif linux_runtime_profile is None or not fallback_matches:
                fallback_count = len(fallback_matches)
                return (
                    False,
                    None,
                    (
                        f"Asset glob '{pattern}' matched 0 assets; compatible Linux archive fallback "
                        f"matched {fallback_count}; exactly one is required"
                    ),
                )

        # A scheduled check supplies a freshly detected profile. If the stored
        # glob points at RT3 but the host now needs RT4 (or vice versa), select
        # the sibling from the same asset family instead of pinning stale ABI
        # metadata forever. Registration helpers omit the profile and retain
        # their explicit asset/glob selection.
        if linux_runtime_profile is not None:
            selected, matches, selection_error = _select_update_runtime_assets(
                item, assets, matches, fallback_matches, pattern, linux_runtime_profile
            )
            if not selected:
                return False, None, selection_error

        if len(matches) != 1:
            return (
                False,
                None,
                f"Asset glob '{pattern}' matched {len(matches)} assets; exactly one is required",
            )
        return (
            True,
            {
                "release_id": str(data.get("id") or ""),
                "version": data.get("tag_name") or "unknown",
                "asset": matches[0],
            },
            "",
        )

    async def _latest_metamod(self, server: Server) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        manager = host._ssh_manager()
        connected, message = await manager.connect(server)
        if not connected:
            return False, None, message
        try:
            success, value = await manager._fetch_latest_metamod_url(None)
            if not success:
                return False, None, value
            url = value.strip()
            filename = url.rsplit("/", 1)[-1]
            return (
                True,
                {
                    "release_id": url,
                    "version": filename,
                    "asset": {"browser_download_url": url, "name": filename},
                },
                "",
            )
        finally:
            await manager.disconnect()
