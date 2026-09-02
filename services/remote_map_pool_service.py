"""Safe remote MapChooser map-pool synchronization."""

from __future__ import annotations

import asyncio
import ipaddress
import posixpath
import shlex
import socket
import uuid
from urllib.parse import urljoin, urlsplit

import httpx

from services.map_management_service import MAX_MAPS_CONFIG_BYTES, MapConfigError, parse_maps_config

MAX_REMOTE_MAP_URL_LENGTH = 4096
MAX_REMOTE_MAP_REDIRECTS = 5
REMOTE_MAP_TIMEOUT_SECONDS = 20


class RemoteMapPoolError(ValueError):
    """Raised when a remote map pool cannot be fetched, validated, or installed."""


def _validate_remote_map_url_syntax(url: str) -> tuple[str, str, int]:
    candidate = (url or "").strip()
    if not candidate or len(candidate) > MAX_REMOTE_MAP_URL_LENGTH:
        raise RemoteMapPoolError(
            f"URL is required and cannot exceed {MAX_REMOTE_MAP_URL_LENGTH} characters"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise RemoteMapPoolError("URL cannot contain control characters")

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise RemoteMapPoolError(f"Invalid URL: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteMapPoolError("Only absolute HTTP and HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteMapPoolError("URLs containing embedded credentials are not supported")
    if parsed.fragment:
        raise RemoteMapPoolError("URL fragments are not supported")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise RemoteMapPoolError("Localhost URLs are not allowed")

    resolved_port = port or (443 if scheme == "https" else 80)
    return candidate, hostname, resolved_port


def _resolve_hostname(hostname: str, port: int) -> set[str]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RemoteMapPoolError("Unable to resolve the remote map-pool hostname") from exc
    return {str(record[4][0]).split("%", 1)[0] for record in records}


async def validate_remote_map_url(url: str) -> str:
    candidate, hostname, port = _validate_remote_map_url_syntax(url)
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = await asyncio.to_thread(_resolve_hostname, hostname, port)
    else:
        addresses = {str(literal_address)}

    if not addresses:
        raise RemoteMapPoolError("The remote map-pool hostname did not resolve")
    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise RemoteMapPoolError("The remote map-pool hostname resolved incorrectly") from exc
        if not address.is_global:
            raise RemoteMapPoolError("Remote map-pool URLs must resolve to public IP addresses")
    return candidate


async def fetch_remote_map_pool(url: str) -> str:
    current_url = await validate_remote_map_url(url)
    timeout = httpx.Timeout(REMOTE_MAP_TIMEOUT_SECONDS)

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": "UpKK-CS2-ServerManager"},
        ) as client:
            for redirect_count in range(MAX_REMOTE_MAP_REDIRECTS + 1):
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        if redirect_count >= MAX_REMOTE_MAP_REDIRECTS:
                            raise RemoteMapPoolError(
                                "Remote map-pool URL redirected too many times"
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise RemoteMapPoolError(
                                "Remote map-pool URL returned an invalid redirect"
                            )
                        current_url = await validate_remote_map_url(urljoin(current_url, location))
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise RemoteMapPoolError(
                            f"Remote map-pool request failed with HTTP {response.status_code}"
                        )

                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > MAX_MAPS_CONFIG_BYTES:
                            raise RemoteMapPoolError(
                                "Remote maps.txt exceeds the 15 MiB size limit"
                            )
                    break
            else:  # Defensive; the redirect limit always terminates the loop.
                raise RemoteMapPoolError("Unable to download the remote map pool")
    except RemoteMapPoolError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise RemoteMapPoolError("Unable to download the remote map pool") from exc

    try:
        content = bytes(payload).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RemoteMapPoolError("Remote maps.txt must be UTF-8 text") from exc
    try:
        parse_maps_config(content)
    except MapConfigError as exc:
        raise RemoteMapPoolError(f"Remote maps.txt is invalid: {exc}") from exc
    return content


def mapchooser_remote_paths(server) -> dict[str, str]:
    csgo_directory = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    counterstrikesharp = posixpath.join(csgo_directory, "addons/counterstrikesharp")
    plugin_directory = posixpath.join(counterstrikesharp, "plugins/MapChooser")
    return {
        "plugin_directory": plugin_directory,
        "plugin_dll": posixpath.join(plugin_directory, "MapChooser.dll"),
        "maps": posixpath.join(counterstrikesharp, "configs/plugins/MapChooser/maps.txt"),
    }


async def replace_remote_map_pool(ssh_manager, server, content: str) -> None:
    parse_maps_config(content)
    maps_path = mapchooser_remote_paths(server)["maps"]
    parent_directory = posixpath.dirname(maps_path)
    success, stdout, stderr = await ssh_manager.execute_command(
        f"mkdir -p -- {shlex.quote(parent_directory)}",
        timeout=20,
    )
    if not success:
        detail = (stderr or stdout or "unable to create MapChooser config directory").strip()
        raise RemoteMapPoolError(f"Unable to prepare maps.txt: {detail}")

    temporary_path = f"{maps_path}.upkk-{uuid.uuid4().hex}.tmp"
    success, error = await ssh_manager.write_file(temporary_path, content, server)
    if not success:
        raise RemoteMapPoolError(f"Unable to stage maps.txt: {error}")

    success, stdout, stderr = await ssh_manager.execute_command(
        f"mv -f -- {shlex.quote(temporary_path)} {shlex.quote(maps_path)}",
        timeout=20,
    )
    if not success:
        await ssh_manager.execute_command(f"rm -f -- {shlex.quote(temporary_path)}", timeout=10)
        detail = (stderr or stdout or "atomic replace failed").strip()
        raise RemoteMapPoolError(f"Unable to replace maps.txt: {detail}")


async def synchronize_remote_map_pool(ssh_manager, server, url: str) -> tuple[str, int]:
    plugin_dll = shlex.quote(mapchooser_remote_paths(server)["plugin_dll"])
    success, _, _ = await ssh_manager.execute_command(f"test -f {plugin_dll}", timeout=10)
    if not success:
        raise RemoteMapPoolError(
            "MapChooser is not installed. Reinstall the plugin before synchronizing maps."
        )

    content = await fetch_remote_map_pool(url)
    await replace_remote_map_pool(ssh_manager, server, content)
    return content, len(parse_maps_config(content).maps)
