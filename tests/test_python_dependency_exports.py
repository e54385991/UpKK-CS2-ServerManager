"""Production exports must select one Pydantic pair on stable and RC Python."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

from scripts.export_requirements import preserve_minor_version_markers

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("python_version", "full_version", "pydantic", "core"),
    [
        ("3.14", "3.14.7", "2.13.5", "2.46.5"),
        ("3.15", "3.15.0rc3", "2.14.0b2", "2.49.0"),
        ("3.15", "3.15.0", "2.14.0b2", "2.49.0"),
    ],
)
def test_export_selects_one_pydantic_pair(python_version, full_version, pydantic, core):
    environment = {
        **default_environment(),
        "python_version": python_version,
        "python_full_version": full_version,
    }
    lines = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    for name, version in [("pydantic", pydantic), ("pydantic-core", core)]:
        candidates = [
            Requirement(line.removesuffix(" \\")) for line in lines if line.startswith(f"{name}==")
        ]
        selected = [req for req in candidates if req.marker.evaluate(environment)]
        assert len(selected) == 1
        assert str(selected[0].specifier) == f"=={version}"

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    roots = [Requirement(value) for value in project["project"]["dependencies"]]
    selected = [req for req in roots if req.name == "pydantic" and req.marker.evaluate(environment)]
    assert len(selected) == 1
    assert selected[0].specifier.contains(pydantic, prereleases=True)


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
