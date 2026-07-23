"""Security and validation tests for custom remote MapChooser pools."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from services.map_management_service import DEFAULT_MAPS_CONFIG, MAX_MAPS_CONFIG_BYTES
from services.remote_map_pool_service import (
    RemoteMapPoolError,
    fetch_remote_map_pool,
    validate_remote_map_url,
)


@pytest.mark.asyncio
async def test_remote_map_url_rejects_local_and_private_targets():
    with pytest.raises(RemoteMapPoolError, match="Localhost"):
        await validate_remote_map_url("http://localhost/maps.txt")

    with pytest.raises(RemoteMapPoolError, match="public IP"):
        await validate_remote_map_url("http://127.0.0.1/maps.txt")

    with (
        patch(
            "services.remote_map_pool_service._resolve_hostname",
            return_value={"10.20.30.40"},
        ),
        pytest.raises(RemoteMapPoolError, match="public IP"),
    ):
        await validate_remote_map_url("https://maps.example.com/maps.txt")


@pytest.mark.asyncio
async def test_remote_map_pool_downloads_and_validates_keyvalues():
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=DEFAULT_MAPS_CONFIG, request=request)

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    with (
        patch(
            "services.remote_map_pool_service._resolve_hostname",
            return_value={"93.184.216.34"},
        ),
        patch("services.remote_map_pool_service.httpx.AsyncClient", side_effect=client_factory),
    ):
        content = await fetch_remote_map_pool("https://maps.example.com/maps.txt")

    assert content == DEFAULT_MAPS_CONFIG


@pytest.mark.asyncio
async def test_remote_map_pool_accepts_valid_response_above_previous_one_mib_limit():
    real_client = httpx.AsyncClient
    content = '"Maplist"\n{\n' + (" " * (1024 * 1024)) + "}\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=content, request=request)

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    with (
        patch(
            "services.remote_map_pool_service._resolve_hostname",
            return_value={"93.184.216.34"},
        ),
        patch("services.remote_map_pool_service.httpx.AsyncClient", side_effect=client_factory),
    ):
        downloaded = await fetch_remote_map_pool("https://maps.example.com/maps.txt")

    assert downloaded == content


@pytest.mark.asyncio
async def test_remote_map_pool_rejects_oversized_response():
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (MAX_MAPS_CONFIG_BYTES + 1),
            request=request,
        )

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    with (
        patch(
            "services.remote_map_pool_service._resolve_hostname",
            return_value={"93.184.216.34"},
        ),
        patch("services.remote_map_pool_service.httpx.AsyncClient", side_effect=client_factory),
        pytest.raises(RemoteMapPoolError, match="15 MiB"),
    ):
        await fetch_remote_map_pool("https://maps.example.com/maps.txt")
