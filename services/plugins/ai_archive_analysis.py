"""Bounded release evidence and validated automatic AI installation rules."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from modules.plugin_ai import InstallationConfig, InstallationMapping, RepositoryAnalysis
from services.plugins.archive_mapping import detect_mapping, validate_mapping
from services.plugins.github_assets import GitHubPlanError, validate_download_url
from services.plugins.release_archive import inspect_release_asset_layout
from services.plugins.tracking import derive_asset_glob

MAX_INSPECTED_ASSETS = 3
MAX_EVIDENCE_ENTRIES = 1500
MAX_EVIDENCE_BYTES = 160_000


def release_candidates(release: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = []
    for asset in (release or {}).get("assets", []):
        name = str(asset.get("name") or "")
        lowered = name.casefold()
        if (
            not lowered.endswith((".zip", ".tar.gz", ".tgz", ".tar", ".7z"))
            or any(
                word in lowered
                for word in ("windows", "win64", "win32", "macos", "debug", "source", "arm64")
            )
            or int(asset.get("size") or 0) > 128 * 1024 * 1024
        ):
            continue
        url = str(asset.get("browser_download_url") or "")
        try:
            validate_download_url(url)
        except GitHubPlanError:
            continue
        result.append({"name": name, "url": url})
    return sorted(result, key=lambda item: ("linux" not in item["name"].casefold(), item["name"]))[
        :MAX_INSPECTED_ASSETS
    ]


async def inspect_archives(
    release: dict[str, Any] | None, repository: str
) -> tuple[list[dict[str, Any]], list[str]]:
    archives, notes = [], []
    if not release or not release.get("assets"):
        notes.append("No stable release archive; manual installation review required")
    for asset in release_candidates(release):
        try:
            layout = await inspect_release_asset_layout(asset, repository.rsplit("/", 1)[-1])
        except GitHubPlanError:
            notes.append(f"Release archive could not be safely inspected: {asset['name']}")
            continue
        archives.append(
            {"asset": asset["name"], "release_tag": (release or {}).get("tag_name"), **layout}
        )
    return archives, notes


def evidence(archives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound prompt size without presenting a truncated tree as complete."""
    result = []
    remaining = MAX_EVIDENCE_BYTES
    for archive in archives:
        entries = []
        for item in archive["entries"][:MAX_EVIDENCE_ENTRIES]:
            record = {key: item[key] for key in ("path", "size", "is_dir", "text") if key in item}
            size = len(str(record).encode())
            if size > remaining:
                break
            remaining -= size
            entries.append(record)
        result.append(
            {
                "asset": archive["asset"],
                "entries": entries,
                "truncated": len(entries) != len(archive["entries"]),
                "detected_mapping": archive["mapping"],
            }
        )
    return result


def configure(
    analysis: RepositoryAnalysis, archives: list[dict[str, Any]], notes: list[str]
) -> InstallationConfig | None:
    """The model proposes; real archive paths decide what can be saved."""
    proposed = analysis.installation
    for archive in archives:
        if proposed and not fnmatchcase(archive["asset"], proposed.asset_glob):
            continue
        _, detected, _ = detect_mapping(archive["entries"], analysis.title, analysis.framework)
        candidates = [detected]
        if proposed:
            candidates.append([rule.model_dump() for rule in proposed.mappings])
            if proposed.target_path:
                candidates.append(
                    [{"source": proposed.source_prefix or ".", "target": proposed.target_path}]
                )
        valid = _validated_candidate(archive["entries"], candidates, analysis.framework)
        if valid is None:
            continue
        # A generated rule is only offered for the archive that was examined.
        # Automatic rules re-detect later layouts before falling back to these
        # validated paths, so a stale source prefix cannot override standard trees.
        pattern = (
            derive_asset_glob(archive["asset"], archive.get("release_tag")) or archive["asset"]
        )
        notes.append(f"Installation mapping verified against release archive: {archive['asset']}")
        return InstallationConfig(
            asset_glob=pattern,
            automatic=True,
            mappings=[InstallationMapping.model_validate(rule) for rule in valid],
        )
    notes.append(
        "No release mapping could be verified; archive auto-detection is required at installation"
    )
    return None


def _validated_candidate(
    entries: list[dict[str, Any]], candidates: list[list[dict[str, str]]], framework: str
) -> list[dict[str, str]] | None:
    for candidate in candidates:
        try:
            valid = validate_mapping(entries, candidate)
            forbidden = "addons/counterstrikesharp" if framework == "swiftly" else "addons/swiftly"
            if framework in {"swiftly", "counterstrikesharp"} and any(
                rule["target"].startswith(forbidden) for rule in valid
            ):
                continue
            return valid
        except ValueError:
            continue
    return None
