"""Archive evidence, ecosystem selection and actual staged filesystem results."""

import asyncio
import io
import json
import tarfile
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import MarketPlugin
from modules.plugin_ai import (
    InstallationConfig,
    InstallationMapping,
    PluginAIInfo,
    RepositoryAnalysis,
)
from services.plugins import ai_archive_analysis as analysis
from services.plugins import ai_install_policy as policy
from services.plugins import release_archive as archive
from services.plugins.archive_mapping import detect_mapping, validate_mapping
from services.plugins.github_assets import GitHubPlanError
from services.plugins.install_mapping import stage_mapping


def entry(path, text=None):
    return {"path": path, "is_dir": False, "size": 1, **({"text": text} if text else {})}


def result(framework="swiftly", installation=None):
    return RepositoryAnalysis(
        is_plugin=True,
        title="Plugin",
        description="Plugin",
        category="utility",
        framework=framework,
        installation=installation,
    )


@pytest.mark.parametrize("root", ["counterstrikesharp", "swiftlys2", "swiftly"])
def test_deep_runtime_tree_does_not_depend_on_zip_directory_entries(root):
    prefix = "release/linux/build/package/" + root
    entries = [entry(prefix + "/plugins/P/P.dll"), entry(prefix + "/configs/P.json")]
    source, mapping, required = detect_mapping(entries, "unrelated-repo-name")
    assert not required and source == prefix
    assert validate_mapping(entries, mapping) == [{"source": prefix, "target": "addons/" + root}]


@pytest.mark.parametrize(
    "framework,root", [("swiftly", "swiftlys2"), ("counterstrikesharp", "counterstrikesharp")]
)
def test_unwrapped_runtime_keeps_sibling_configuration(framework, root):
    entries = [
        entry("build/plugins/P/P.dll"),
        entry("build/gamedata/P.json"),
        entry("build/translations/P/en.json"),
    ]
    _, mapping, required = detect_mapping(entries, "P", framework)
    assert not required
    assert {rule["target"] for rule in validate_mapping(entries, mapping)} == {
        f"addons/{root}/plugins",
        f"addons/{root}/gamedata",
        f"addons/{root}/translations",
    }


def test_unknown_dll_never_defaults_to_css_and_ambiguous_roots_need_evidence():
    assert detect_mapping([entry("P.dll"), entry("P.deps.json")], "P")[2]
    assert detect_mapping([entry("linux/addons/P/P.so"), entry("other/addons/P/P.so")], "P")[2]


@pytest.mark.parametrize("format", ["zip", "tar"])
def test_real_archive_manifest_identifies_swiftly_and_assembly_name(tmp_path, format):
    path = tmp_path / ("plugin.zip" if format == "zip" else "plugin.tar")
    files = {
        "build/publish/ActualPlugin/ActualPlugin.dll": b"binary",
        "build/publish/ActualPlugin/ActualPlugin.deps.json": b'{"libraries":{"SwiftlyS2.CS2/1.0":{}}}',
    }
    if format == "zip":
        with zipfile.ZipFile(path, "w") as handle:
            for name, content in files.items():
                handle.writestr(name, content)
    else:
        with tarfile.open(path, "w") as handle:
            for name, content in files.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                handle.addfile(item, io.BytesIO(content))
    entries = archive._archive_entries(str(path), path.name, path.stat().st_size)
    _, mapping, required = detect_mapping(entries, "different-repository")
    assert not required
    assert mapping == [
        {"source": "build/publish/ActualPlugin", "target": "addons/swiftlys2/plugins/ActualPlugin"}
    ]


def test_native_vdf_and_binary_get_separate_directories():
    entries = [
        entry(
            "package/load/retakes.vdf",
            '"Metamod Plugin" { "file" "addons/retakes/bin/linuxsteamrt64/retakes" }',
        ),
        entry("package/linux/retakes.so"),
    ]
    _, mapping, required = detect_mapping(entries, "Retakes", "other")
    assert not required
    assert validate_mapping(entries, mapping) == [
        {"source": "package/load/retakes.vdf", "target": "addons/metamod"},
        {"source": "package/linux/retakes.so", "target": "addons/retakes/bin/linuxsteamrt64"},
    ]


@pytest.mark.parametrize(
    "mapping",
    [
        [{"source": "missing", "target": "addons/P"}],
        [{"source": ".", "target": "../../etc"}],
        [{"source": ".", "target": "addons"}, {"source": "P.dll", "target": "addons"}],
        [{"source": "P.dll", "target": "addons/P"}],
    ],
)
def test_mapping_rejects_missing_traversal_overlap_and_dropped_config(mapping):
    with pytest.raises(ValueError):
        validate_mapping([entry("P.dll"), entry("P.json")], mapping)


def test_mapping_rejects_repeated_addons_and_file_directory_collisions():
    with pytest.raises(GitHubPlanError, match="duplicates"):
        validate_mapping(
            [entry("addons/P/P.dll")], [{"source": ".", "target": "addons/counterstrikesharp"}]
        )
    with pytest.raises(GitHubPlanError, match="collisions"):
        validate_mapping(
            [entry("a"), entry("b")],
            [{"source": "a", "target": "addons"}, {"source": "b", "target": "addons/a"}],
        )


def test_ai_proposal_is_validated_and_can_map_nonstandard_native_config():
    entries = [
        entry("load/P.vdf", '"file" "addons/P/bin/P"'),
        entry("binary/P.so"),
        entry("defaults/P.cfg"),
    ]
    proposed = InstallationConfig(
        mappings=[
            InstallationMapping(source="load", target="addons/metamod"),
            InstallationMapping(source="binary", target="addons/P/bin"),
            InstallationMapping(source="defaults", target="cfg"),
        ]
    )
    notes = []
    configured = analysis.configure(
        result("other", proposed), [{"asset": "linux.zip", "entries": entries}], notes
    )
    assert configured and configured.automatic and len(configured.mappings) == 3
    assert configured.asset_glob == "linux.zip"
    assert "verified" in notes[0]
    plugin = MarketPlugin(
        title="P",
        github_url="https://github.com/a/p",
        framework="other",
        ai_metadata=PluginAIInfo(model="test", installation=configured).model_dump(),
    )
    applied = policy.apply_layout(
        plugin, {"entries": entries, "mapping": [], "mapping_required": True, "source_prefix": None}
    )
    assert applied["archive_mappings"] == [rule.model_dump() for rule in configured.mappings]
    bad = proposed.model_copy(
        update={"mappings": [InstallationMapping(source="missing", target="addons")]}
    )
    assert (
        analysis.configure(result("other", bad), [{"asset": "linux.zip", "entries": entries}], [])
        is None
    )


@pytest.mark.asyncio
async def test_automatic_upgrade_redetects_changed_wrapper(monkeypatch):
    info = PluginAIInfo(
        model="test",
        installation=InstallationConfig(
            automatic=True,
            mappings=[InstallationMapping(source="old", target="addons/swiftlys2/plugins/P")],
        ),
    )
    plugin = MarketPlugin(
        title="P",
        github_url="https://github.com/a/p",
        framework="swiftly",
        ai_metadata=info.model_dump(),
    )
    entries = [entry("new/package/swiftlys2/plugins/P/P.dll")]
    monkeypatch.setattr(
        archive,
        "inspect_release_asset_layout",
        AsyncMock(
            return_value={
                "entries": entries,
                "mapping": [],
                "mapping_required": True,
                "source_prefix": None,
                "archive_sha256": "a" * 64,
            }
        ),
    )
    rules = await policy.selected_asset_rules(
        plugin, "https://github.com/a/p/releases/download/v2/p.zip"
    )
    assert rules["archive_mappings"] == [
        {"source": "new/package/swiftlys2", "target": "addons/swiftlys2"}
    ]
    assert rules["custom_install_path"] is None


@pytest.mark.asyncio
async def test_staging_really_copies_multiple_sources_without_nesting(tmp_path):
    source, target = tmp_path / "archive with spaces", tmp_path / "staging"
    (source / "native").mkdir(parents=True)
    (source / "native/P.so").write_bytes(b"native")
    (source / "P.vdf").write_text('"file" "addons/P/bin/P"')

    async def execute(command):
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode == 0, stdout.decode(), stderr.decode()

    await stage_mapping(
        SimpleNamespace(execute_command=execute),
        str(source),
        str(target),
        [
            InstallationMapping(source="native", target="addons/P/bin"),
            InstallationMapping(source="P.vdf", target="addons/metamod"),
        ],
    )
    assert (target / "addons/P/bin/P.so").read_bytes() == b"native"
    assert (target / "addons/metamod/P.vdf").is_file()
    assert sorted(
        str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
    ) == ["addons/P/bin/P.so", "addons/metamod/P.vdf"]


@pytest.mark.asyncio
async def test_inspection_bounded_and_invalid_archive_does_not_hide_next(monkeypatch):
    assets = [
        {
            "name": f"linux-{n}.zip",
            "browser_download_url": f"https://github.com/a/p/releases/download/v1/{n}.zip",
        }
        for n in range(8)
    ]
    inspect = AsyncMock(side_effect=[GitHubPlanError("invalid"), {"entries": []}, {"entries": []}])
    monkeypatch.setattr(analysis, "inspect_release_asset_layout", inspect)
    archives, notes = await analysis.inspect_archives({"assets": assets}, "https://github.com/a/p")
    assert inspect.await_count == analysis.MAX_INSPECTED_ASSETS
    assert len(archives) == 2 and len(notes) == 1
    data = analysis.evidence(
        [{"asset": "p.zip", "mapping": [], "entries": [entry("x" * 500)] * 5000}]
    )
    assert data[0]["truncated"] is True
    assert len(json.dumps(data)) < analysis.MAX_EVIDENCE_BYTES + 5000
