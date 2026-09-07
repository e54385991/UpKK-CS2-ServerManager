"""Stage verified multi-directory mappings before the existing copy/rollback path."""

from __future__ import annotations

import shlex

from modules.plugin_ai import InstallationMapping
from services.ssh_manager import SSHManager


def mapping_copy_command(source_root: str, staging_root: str, mapping: InstallationMapping) -> str:
    source = shlex.quote(f"{source_root}/{mapping.source}")
    target = shlex.quote(f"{staging_root}/{mapping.target}")
    return (
        f"mkdir -p -- {target} && test ! -L {source} && "
        f"if test -d {source}; then cp -a -- {source}/. {target}/; "
        f"elif test -f {source}; then cp -a -- {source} {target}/; "
        "else exit 1; fi"
    )


async def stage_mapping(
    ssh: SSHManager, source_root: str, staging_root: str, mappings: list[InstallationMapping]
) -> str:
    ok, _, _ = await ssh.execute_command(f"mkdir -p -- {shlex.quote(staging_root + '/addons')}")
    if not ok:
        raise ValueError("Unable to create archive mapping staging directory")
    for mapping in mappings:
        ok, _, _ = await ssh.execute_command(
            mapping_copy_command(source_root, staging_root, mapping)
        )
        if not ok:
            raise ValueError("Approved archive mapping source could not be staged")
    return staging_root
