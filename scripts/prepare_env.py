"""Create or incrementally update the project ``.env`` file.

This helper deliberately uses only the Python standard library so it can run
as soon as ``uv`` has prepared the project interpreter.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*)"
    r"(?P<value>.*?)(?P<newline>\r?\n)?$"
)
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SECRET_KEYS = ("SECRET_KEY", "JWT_SECRET_KEY", "TOKEN_HASH_KEY")
PRIVATE_ENV_MODE = 0o600
PLACEHOLDER_MARKERS = (
    "change-this",
    "changeme",
    "example-placeholder",
    "replace-with",
    "replace_with",
    "your-jwt",
    "your-secret",
    "your_jwt",
    "your_secret",
    "请替换",
    "示例",
)


class EnvironmentPreparationError(ValueError):
    """The environment file contains unsafe or contradictory key settings."""


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _split_inline_comment(raw: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "#" and (index == 0 or raw[index - 1].isspace()):
            comment_start = index
            while comment_start > 0 and raw[comment_start - 1] in " \t":
                comment_start -= 1
            return raw[:comment_start], raw[comment_start:]
    return raw, ""


def _dotenv_value(raw: str) -> str:
    value, _comment = _split_inline_comment(raw)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _is_placeholder(value: str) -> bool:
    normalized = _dotenv_value(value)
    if not normalized:
        return True
    lowered = normalized.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS) or (
        normalized.startswith("<") and normalized.endswith(">")
    )


def _assignments(lines: list[str]) -> dict[str, tuple[int, str]]:
    assignments: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(lines):
        match = ASSIGNMENT_RE.match(line)
        if match:
            assignments[match.group("key")] = (index, match.group("value"))
    return assignments


def _example_assignments(example_text: str) -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in example_text.splitlines():
        match = ASSIGNMENT_RE.match(line)
        if not match:
            continue
        key = match.group("key")
        if key in seen:
            continue
        seen.add(key)
        assignments.append((key, match.group("value")))
    return assignments


def _replace_value(lines: list[str], key: str, value: str) -> None:
    index, _old_value = _assignments(lines)[key]
    match = ASSIGNMENT_RE.match(lines[index])
    if match is None:  # pragma: no cover - protected by _assignments
        raise EnvironmentPreparationError(f"Unable to update {key}")

    old_value = match.group("value")
    _value, comment = _split_inline_comment(old_value)
    if comment and not comment[0].isspace():
        comment = f" {comment}"
    newline = match.group("newline") or ""
    lines[index] = f"{match.group('prefix')}{value}{comment}{newline}"


def _decode_aes_key(encoded: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded):
        raise EnvironmentPreparationError("Credential encryption keys must use URL-safe base64")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except ValueError as exc:
        raise EnvironmentPreparationError(
            "Credential encryption keys must use URL-safe base64"
        ) from exc
    if len(decoded) != 32:
        raise EnvironmentPreparationError(
            "Credential encryption keys must decode to exactly 32 bytes"
        )
    return decoded


def _validated_keyring(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(_dotenv_value(value))
    except json.JSONDecodeError as exc:
        raise EnvironmentPreparationError("CREDENTIAL_ENCRYPTION_KEYS must be valid JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise EnvironmentPreparationError(
            "CREDENTIAL_ENCRYPTION_KEYS must be a non-empty JSON object"
        )

    keyring: dict[str, str] = {}
    for raw_key_id, raw_encoded in parsed.items():
        key_id = str(raw_key_id)
        if not KEY_ID_RE.fullmatch(key_id):
            raise EnvironmentPreparationError(f"Invalid credential encryption key id: {key_id!r}")
        if not isinstance(raw_encoded, str):
            raise EnvironmentPreparationError(
                f"Credential encryption key {key_id!r} must be a string"
            )
        _decode_aes_key(raw_encoded)
        keyring[key_id] = raw_encoded
    return keyring


def _new_aes_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _prepare_content(existing_text: str | None, example_text: str) -> tuple[str, list[str]]:
    created = existing_text is None
    text = example_text if created else existing_text
    lines = text.splitlines(keepends=True)
    changed: list[str] = []

    assignments = _assignments(lines)
    missing = [
        (key, value) for key, value in _example_assignments(example_text) if key not in assignments
    ]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        if lines and any(line.strip() for line in lines):
            lines.append("\n")
        lines.append("# Added by upgrade.sh from .env.example\n")
        lines.extend(f"{key}={value}\n" for key, value in missing)
        changed.extend(key for key, _value in missing)

    for key in SECRET_KEYS:
        assignments = _assignments(lines)
        if key not in assignments:
            raise EnvironmentPreparationError(f"{key} is missing from .env.example")
        if _is_placeholder(assignments[key][1]):
            _replace_value(lines, key, secrets.token_urlsafe(48))
            changed.append(key)

    assignments = _assignments(lines)
    keyring_value = assignments.get("CREDENTIAL_ENCRYPTION_KEYS")
    active_value = assignments.get("CREDENTIAL_ACTIVE_KEY_ID")
    if keyring_value is None or active_value is None:
        raise EnvironmentPreparationError(
            "Credential encryption settings are missing from .env.example"
        )

    raw_keyring = keyring_value[1]
    raw_active = active_value[1]
    active_placeholder = _is_placeholder(raw_active)
    if _is_placeholder(raw_keyring) or _dotenv_value(raw_keyring) == "{}":
        requested_id = _dotenv_value(raw_active)
        key_id = (
            requested_id if not active_placeholder and KEY_ID_RE.fullmatch(requested_id) else "v1"
        )
        keyring = {key_id: _new_aes_key()}
        _replace_value(
            lines,
            "CREDENTIAL_ENCRYPTION_KEYS",
            json.dumps(keyring, separators=(",", ":")),
        )
        changed.append("CREDENTIAL_ENCRYPTION_KEYS")
    else:
        keyring = _validated_keyring(raw_keyring)

    if active_placeholder:
        active_key_id = next(iter(keyring))
        _replace_value(lines, "CREDENTIAL_ACTIVE_KEY_ID", active_key_id)
        changed.append("CREDENTIAL_ACTIVE_KEY_ID")
    else:
        active_key_id = _dotenv_value(raw_active)
        if not KEY_ID_RE.fullmatch(active_key_id):
            raise EnvironmentPreparationError("CREDENTIAL_ACTIVE_KEY_ID contains an invalid key id")
        if active_key_id not in keyring:
            raise EnvironmentPreparationError(
                "CREDENTIAL_ACTIVE_KEY_ID must reference CREDENTIAL_ENCRYPTION_KEYS"
            )

    # Validate generated and existing values through the same strict path.
    final_assignments = _assignments(lines)
    _validated_keyring(final_assignments["CREDENTIAL_ENCRYPTION_KEYS"][1])

    if created:
        changed.insert(0, ".env")
    return "".join(lines), list(dict.fromkeys(changed))


@contextmanager
def _environment_lock(env_path: Path):
    digest = hashlib.sha256(str(env_path.resolve()).encode("utf-8")).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"cs2-manager-env-{digest}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def prepare_environment(env_path: Path, example_path: Path) -> list[str]:
    """Merge the template and replace only empty/example security values."""
    env_path = env_path.resolve()
    example_path = example_path.resolve()
    if not example_path.is_file():
        raise EnvironmentPreparationError(
            f"Environment template not found: {_display_path(example_path)}"
        )

    with _environment_lock(env_path):
        existing_text = env_path.read_text(encoding="utf-8") if env_path.exists() else None
        example_text = example_path.read_text(encoding="utf-8")
        updated_text, changed = _prepare_content(existing_text, example_text)
        permissions_changed = (
            env_path.exists() and (env_path.stat().st_mode & 0o777) != PRIVATE_ENV_MODE
        )
        if existing_text == updated_text:
            if permissions_changed:
                env_path.chmod(PRIVATE_ENV_MODE)
                return [".env permissions"]
            return []

        env_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as temporary_file:
                temporary_file.write(updated_text)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary.chmod(PRIVATE_ENV_MODE)
            os.replace(temporary, env_path)
        finally:
            temporary.unlink(missing_ok=True)
        if permissions_changed:
            changed.append(".env permissions")
        return changed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--example-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        changed = prepare_environment(args.env_file, args.example_file)
    except (EnvironmentPreparationError, OSError) as exc:
        print(f"Environment preparation failed: {exc}", file=sys.stderr)
        return 2

    if changed:
        print(f"Prepared {_display_path(args.env_file)} ({', '.join(changed)}).")
    else:
        print(f"Environment file {_display_path(args.env_file)} is already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
