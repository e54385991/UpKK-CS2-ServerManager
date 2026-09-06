"""Shell commands that stage, copy, back up, and roll back a plugin install."""

from __future__ import annotations

import re
import shlex
import uuid


def operation_token(operation_id: str | None) -> str:
    """Return a shell-safe, bounded identifier for one installation attempt."""
    raw = str(operation_id or "").strip()
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-_.")[:80]
    return token or uuid.uuid4().hex


def remote_plugin_temp_dir(server_id: int, operation_id: str | None) -> str:
    """Build an isolated remote staging directory for one installation."""
    return f"/tmp/upkk-plugin-{server_id}-{operation_token(operation_id)}"


def build_plugin_copy_command(
    source_dir: str,
    target_dir: str,
    exclude_patterns: list[str],
    *,
    use_rsync: bool,
) -> str:
    """Build a plugin copy command while always refreshing gamedata files.

    Upgrade exclusions intentionally preserve user configuration files by
    extension.  Framework gamedata often uses those same extensions (for
    example CounterStrikeSharp's ``gamedata.json`` and CS2Fixes'
    ``cs2fixes.jsonc``), so a final, unconditional copy of every file below a
    ``gamedata`` directory is required after the filtered copy.
    """
    safe_source = shlex.quote(source_dir)
    safe_target = shlex.quote(target_dir)

    if use_rsync:
        exclusions = "".join(f" --exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"rsync -av{exclusions} {safe_source}/ {safe_target}/"
    elif exclude_patterns:
        exclusions = " ".join(f"--exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"cd {safe_source} && tar {exclusions} -cf - . | tar -xf - -C {safe_target}"
    else:
        primary_copy = f"cp -r {safe_source}/. {safe_target}/"

    # GNU find's batched -exec form propagates a non-zero copy status.  Rebuild
    # each archive-relative parent path below the destination, then force the
    # file copy so gamedata is immune to every upgrade/manual exclusion rule.
    copy_script = (
        'target="$1"; shift; '
        "for source do "
        'relative=${source#./}; destination="$target/$relative"; '
        'parent=${destination%/*}; mkdir -p "$parent" '
        "&& cp -a --no-dereference --remove-destination -- "
        '"$source" "$destination" || exit 1; '
        "done"
    )
    gamedata_copy = (
        f"cd {safe_source} && "
        "find . -path '*/gamedata/*' -type f "
        f"-exec sh -c {shlex.quote(copy_script)} sh {safe_target} {{}} +"
    )
    return f"{primary_copy} && {gamedata_copy}"


def build_backup_command(source_dir: str, target_dir: str, backup_dir: str) -> str:
    script = (
        'source="$1"; target="$2"; backup="$3"; '
        'manifest="$backup/manifest.tsv"; files="$backup/source-files.txt"; '
        'mkdir -p -- "$backup/files"; : > "$manifest"; '
        'cd "$source" || exit 1; find . -type f -print > "$files" || exit 1; '
        "while IFS= read -r relative; do relative=${relative#./}; "
        'case "$relative" in ""|/*|*".."*) exit 91;; esac; '
        'destination="$target/$relative"; saved="$backup/files/$relative"; '
        'if test -L "$destination"; then exit 92; '
        'elif test -e "$destination"; then mkdir -p -- "${saved%/*}" '
        '&& cp -a --no-dereference -- "$destination" "$saved" || exit 1; '
        'printf "existing\\t%s\\n" "$relative" >> "$manifest"; '
        'else printf "new\\t%s\\n" "$relative" >> "$manifest"; fi; '
        'done < "$files"'
    )
    return (
        f"sh -c {shlex.quote(script)} sh {shlex.quote(source_dir)} "
        f"{shlex.quote(target_dir)} {shlex.quote(backup_dir)}"
    )


def build_rollback_command(target_dir: str, backup_dir: str) -> str:
    script = (
        'target="$1"; backup="$2"; manifest="$backup/manifest.tsv"; '
        'test -f "$manifest" || exit 1; '
        'while IFS="$(printf "\\t")" read -r state relative; do '
        'case "$relative" in ""|/*|*".."*) exit 91;; esac; '
        'destination="$target/$relative"; '
        'if test "$state" = new; then rm -f -- "$destination" || exit 1; '
        'elif test "$state" = existing; then saved="$backup/files/$relative"; '
        'mkdir -p -- "${destination%/*}" && cp -a --no-dereference '
        '--remove-destination -- "$saved" "$destination" || exit 1; fi; '
        'done < "$manifest"'
    )
    return f"sh -c {shlex.quote(script)} sh {shlex.quote(target_dir)} {shlex.quote(backup_dir)}"
