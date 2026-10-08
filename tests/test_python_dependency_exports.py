"""Production exports must select one Pydantic pair on stable and RC Python."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

from scripts import export_requirements
from scripts.export_requirements import preserve_minor_version_markers

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("python_version", "full_version"),
    [
        ("3.14", "3.14.7"),
        ("3.15", "3.15.0rc3"),
        ("3.15", "3.15.0"),
    ],
)
def test_export_selects_one_pydantic_pair(python_version, full_version):
    environment = {
        **default_environment(),
        "python_version": python_version,
        "python_full_version": full_version,
    }
    lines = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    exported = {}
    for name in ("pydantic", "pydantic-core"):
        candidates = [
            Requirement(line.removesuffix(" \\")) for line in lines if line.startswith(f"{name}==")
        ]
        selected = [req for req in candidates if req.marker.evaluate(environment)]
        assert len(selected) == 1
        exported[name] = selected[0]

    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    parents = [
        package
        for package in lock["package"]
        if package["name"] == "pydantic"
        and str(exported["pydantic"].specifier) == f"=={package['version']}"
    ]
    assert len(parents) == 1
    core = next(dep for dep in parents[0]["dependencies"] if dep["name"] == "pydantic-core")
    core_versions = (
        {core["version"]}
        if "version" in core
        else {
            package["version"] for package in lock["package"] if package["name"] == "pydantic-core"
        }
    )
    assert len(core_versions) == 1
    assert str(exported["pydantic-core"].specifier) == f"=={core_versions.pop()}"

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    roots = [Requirement(value) for value in project["project"]["dependencies"]]
    selected = [req for req in roots if req.name == "pydantic" and req.marker.evaluate(environment)]
    assert len(selected) == 1
    assert selected[0].specifier.contains(parents[0]["version"], prereleases=True)


def test_export_preserves_patch_constraints_and_hashes():
    source = (
        "package==1.0; python_full_version >= '3.15' and python_full_version < '3.16' "
        "and python_full_version != '3.15.1' and sys_platform == 'linux' \\\n"
        "    --hash=sha256:abc123\n"
    )
    result = preserve_minor_version_markers(source)
    assert result == source.replace("python_full_version >=", "python_version >=").replace(
        "python_full_version <", "python_version <"
    )
    assert preserve_minor_version_markers("package==1.0; python_full_version >= '3.15.1'") == (
        "package==1.0; python_full_version >= '3.15.1'"
    )


@pytest.mark.parametrize("drift", ["core-version", "hash", "missing"])
def test_export_check_rejects_drift_without_rewriting(tmp_path, monkeypatch, capsys, drift):
    content = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    destination = tmp_path / "requirements.txt"
    if drift == "core-version":
        core_line = next(
            line for line in content.splitlines() if line.startswith("pydantic-core==")
        )
        core_pin = core_line.split(" ; ")[0]
        corrupted = content.replace(
            core_pin,
            "pydantic-core==0.0.0",
            1,
        )
    else:
        corrupted = content.replace("--hash=sha256:", "--hash=sha256:0", 1)
    assert corrupted != content
    if drift != "missing":
        destination.write_text(corrupted, encoding="utf-8")
    monkeypatch.setattr(export_requirements, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_requirements, "export_requirements", lambda: content)

    assert export_requirements.main(["--check"]) == 1
    assert "Regenerate with:" in capsys.readouterr().out
    if drift == "missing":
        assert not destination.exists()
    else:
        assert destination.read_text(encoding="utf-8") == corrupted


def test_export_check_accepts_matching_content_without_writing(tmp_path, monkeypatch):
    destination = tmp_path / "requirements.txt"
    content = "# locked export\n"
    destination.write_text(content, encoding="utf-8")
    before = destination.stat().st_mtime_ns
    monkeypatch.setattr(export_requirements, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_requirements, "export_requirements", lambda: content)

    assert export_requirements.main(["--check"]) == 0
    assert destination.stat().st_mtime_ns == before


def test_export_regeneration_replaces_stale_content(tmp_path, monkeypatch):
    destination = tmp_path / "requirements.txt"
    destination.write_text("stale export", encoding="utf-8")
    content = "# regenerated export\n"
    monkeypatch.setattr(export_requirements, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_requirements, "export_requirements", lambda: content)

    assert export_requirements.main([]) == 0
    assert destination.read_text(encoding="utf-8") == content
