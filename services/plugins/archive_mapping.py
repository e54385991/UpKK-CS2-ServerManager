"""Evidence-based release layouts shared by discovery, preflight and upgrades."""

from __future__ import annotations

import posixpath
import re
from typing import Any, Literal

from modules.plugin_ai import InstallationMapping
from services.plugins.github_assets import GitHubPlanError

RUNTIME_ROOTS = {"counterstrikesharp": "counterstrikesharp", "swiftly": "swiftlys2"}
FRAMEWORK_DIRS = {"counterstrikesharp", "swiftlys2", "swiftly"}
RUNTIME_SIBLINGS = {"plugins", "gamedata", "configs", "translations", "shared", "extensions"}


def _parents(paths: list[str]) -> list[str]:
    return sorted(
        {
            "",
            *(posixpath.dirname(path) for path in paths),
            *(
                "/".join(path.split("/")[:depth])
                for path in paths
                for depth in range(1, min(12, len(path.split("/"))))
            ),
        },
        key=lambda value: (value.count("/"), len(value), value),
    )


def _under(path: str, prefix: str) -> bool:
    return not prefix or path.startswith(prefix + "/")


def _unique(
    candidates: list[tuple[str | None, list[dict[str, str]]]],
) -> tuple[str | None, list[dict[str, str]], bool]:
    if len(candidates) == 1:
        return *candidates[0], False
    return None, [], True


def runtime_from_entries(
    entries: list[dict[str, Any]],
) -> Literal["counterstrikesharp", "swiftly"] | None:
    evidence = " ".join(str(item.get("text", "")) for item in entries).casefold()
    css = "counterstrikesharp.api" in evidence
    swiftly = any(
        marker in evidence for marker in ("swiftlys2.core", "swiftlys2.cs2", "swiftlys2.shared")
    )
    if css != swiftly:
        return "counterstrikesharp" if css else "swiftly"
    return None


def detect_mapping(
    entries: list[dict[str, Any]], repo_name: str, framework: str | None = None
) -> tuple[str | None, list[dict[str, str]], bool]:
    """Never infer a managed runtime from DLL extensions alone."""
    paths = [str(item["path"]) for item in entries if not item.get("is_dir")]
    parents = _parents(paths)
    candidates = []
    for parent in parents:
        base = parent + "/" if parent else ""
        if any(path.startswith(base + "addons/") for path in paths):
            roots = [
                root
                for root in ("addons", "cfg")
                if any(path.startswith(base + root + "/") for path in paths)
            ]
            candidates.append(
                (parent or None, [{"source": base + root, "target": root} for root in roots])
            )
    if candidates:
        return _unique(candidates)
    for parent in parents:
        name = posixpath.basename(parent)
        if name in FRAMEWORK_DIRS and any(
            _under(path, parent) and path.endswith((".dll", ".so")) for path in paths
        ):
            candidates.append((parent, [{"source": parent, "target": "addons/" + name}]))
    if candidates:
        return _unique(candidates)
    # Metamod packages often omit addons but keep metamod/*.vdf alongside
    # <plugin>/bin/*.so. Keep that shared parent rather than losing the loader.
    for parent in parents:
        base = parent + "/" if parent else ""
        if any(path.startswith(base + "metamod/") and path.endswith(".vdf") for path in paths):
            candidates.append((parent or None, [{"source": parent or ".", "target": "addons"}]))
    if candidates:
        return _unique(candidates)
    native = _native_mapping(entries)
    if native:
        return None, native, False
    runtime = runtime_from_entries(entries) or framework
    root = RUNTIME_ROOTS.get(runtime or "")
    if root is None:
        return None, [], True
    return _managed_mapping(paths, parents, root, repo_name)


def _managed_mapping(
    paths: list[str], parents: list[str], root: str, repo_name: str
) -> tuple[str | None, list[dict[str, str]], bool]:
    candidates = []
    for parent in parents:
        base = parent + "/" if parent else ""
        if any(path.startswith(base + "plugins/") and path.endswith(".dll") for path in paths):
            siblings = sorted(
                name
                for name in RUNTIME_SIBLINGS
                if any(path.startswith(base + name + "/") for path in paths)
            )
            rules = [
                {"source": base + name, "target": f"addons/{root}/{name}"} for name in siblings
            ]
            candidates.append((rules[0]["source"] if len(rules) == 1 else None, rules))
    if candidates:
        return _unique(candidates)
    # Match the primary assembly to its deps manifest, not the repository name.
    # Publishing folders may be deeply wrapped (build/publish/<PluginId>).
    rules = []
    for path in paths:
        if not path.endswith(".deps.json") or path.removesuffix(".deps.json") + ".dll" not in paths:
            continue
        parent = posixpath.dirname(path)
        name = posixpath.basename(path).removesuffix(".deps.json")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or repo_name).strip(".-")
        rules.append({"source": parent or ".", "target": f"addons/{root}/plugins/{safe_name}"})
    if len(rules) == 1:
        return (rules[0]["source"] if rules[0]["source"] != "." else None), rules, False
    return None, [], True


def _native_mapping(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """A VDF's explicit game-relative file path anchors a native binary."""
    paths = [str(item["path"]) for item in entries if not item.get("is_dir")]
    rules = []
    for entry in entries:
        if not str(entry["path"]).endswith(".vdf"):
            continue
        match = re.search(r'"file"\s*"(addons/[^"\r\n]+)"', str(entry.get("text", "")))
        if not match:
            continue
        reference = match[1].removesuffix(".so")
        binaries = [
            path
            for path in paths
            if posixpath.basename(path) == posixpath.basename(reference) + ".so"
        ]
        if len(binaries) != 1:
            return []
        rules.extend(
            [
                {"source": str(entry["path"]), "target": "addons/metamod"},
                {"source": binaries[0], "target": posixpath.dirname(reference)},
            ]
        )
    return rules


def validate_mapping(
    entries: list[dict[str, Any]], mapping: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Validate real sources and disjoint destinations before any SSH write."""
    if not mapping or len(mapping) > 20:
        raise GitHubPlanError("Installation needs between 1 and 20 directory mappings")
    rules = [InstallationMapping.model_validate(rule).model_dump() for rule in mapping]
    targets: set[str] = set()
    sources: set[str] = set()
    for rule in rules:
        matched = 0
        for entry in entries:
            if entry.get("is_dir"):
                continue
            path = str(entry["path"])
            source = rule["source"]
            if source == ".":
                remainder = path
            elif path == source:
                remainder = posixpath.basename(path)
            elif _under(path, source):
                remainder = path[len(source) + 1 :]
            else:
                continue
            target = posixpath.normpath(rule["target"] + "/" + remainder)
            if path in sources or target.casefold() in targets:
                raise GitHubPlanError("Installation mapping overlaps sources or destinations")
            if "/addons/" in target or target.split("/", 1)[0] not in {"addons", "cfg"}:
                raise GitHubPlanError("Installation mapping duplicates or escapes the game roots")
            sources.add(path)
            targets.add(target.casefold())
            matched += 1
        if not matched:
            raise GitHubPlanError("Installation mapping source is absent from the release archive")
    payload = {
        str(item["path"])
        for item in entries
        if not item.get("is_dir")
        and str(item["path"]).endswith(
            (".dll", ".so", ".vdf", ".json", ".jsonc", ".cfg", ".ini", ".yaml", ".toml")
        )
    }
    if payload - sources:
        raise GitHubPlanError("Installation mapping omits runtime files or configuration")
    # Avoid a file being copied over a directory created by another mapping.
    for target in targets:
        if any(parent.casefold() in targets for parent in _parents([target]) if parent):
            raise GitHubPlanError("Installation mapping has file/directory target collisions")
    return rules
