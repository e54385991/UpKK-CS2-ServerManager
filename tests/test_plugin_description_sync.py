"""Bulk marketplace description refresh from GitHub READMEs."""

from __future__ import annotations

import base64

import pytest

from modules.models.plugins import MarketPlugin, PluginFramework
from services.plugins import description_sync
from services.plugins.description_sync import sync_market_plugin_descriptions


class _Session:
    """Minimal async session double recording commits and added rows."""

    def __init__(self, rows: list[MarketPlugin]) -> None:
        self._rows = rows
        self.commits = 0
        self.added: list[MarketPlugin] = []
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        rows = self._rows

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    def add(self, row) -> None:
        self.added.append(row)


def _plugin(plugin_id: int, url: str, description: str | None = None) -> MarketPlugin:
    return MarketPlugin(
        id=plugin_id,
        github_url=url,
        title=f"Plugin {plugin_id}",
        description=description,
    )


def _readme(text: str) -> dict:
    return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def _http_get(responses: dict[str, tuple[bool, object, str | None]]):
    async def get(url, **_kwargs):
        return responses[url]

    return get


@pytest.mark.asyncio
async def test_sync_writes_readme_into_description(monkeypatch):
    session = _Session([_plugin(1, "https://github.com/acme/one", "old")])
    monkeypatch.setattr(
        description_sync.http_helper,
        "get",
        _http_get(
            {
                "https://api.github.com/repos/acme/one/readme": (
                    True,
                    _readme("# One\n\nDetailed docs."),
                    None,
                )
            }
        ),
    )

    result = await sync_market_plugin_descriptions(session)

    assert result.updated == 1
    assert result.total == 1
    assert session.added[0].description == "# One\n\nDetailed docs."
    # One commit releases the read transaction before GitHub, one persists.
    assert session.commits == 2


@pytest.mark.asyncio
async def test_sync_reports_unchanged_failed_and_missing_readme(monkeypatch):
    session = _Session(
        [
            _plugin(1, "https://github.com/acme/one", "# One"),
            _plugin(2, "https://github.com/acme/two"),
            _plugin(3, "https://github.com/acme/three"),
        ]
    )
    monkeypatch.setattr(
        description_sync.http_helper,
        "get",
        _http_get(
            {
                "https://api.github.com/repos/acme/one/readme": (
                    True,
                    _readme("# One"),
                    None,
                ),
                "https://api.github.com/repos/acme/two/readme": (False, None, "404"),
                "https://api.github.com/repos/acme/three/readme": (
                    True,
                    _readme("   "),
                    None,
                ),
            }
        ),
    )

    result = await sync_market_plugin_descriptions(session)

    actions = {item.plugin_id: item.action for item in result.items}
    assert actions == {1: "unchanged", 2: "failed", 3: "skipped"}
    assert result.failed == 1
    assert session.added == []
    # Nothing changed, so no second commit is issued.
    assert session.commits == 1


@pytest.mark.asyncio
async def test_sync_without_overwrite_keeps_existing_descriptions(monkeypatch):
    session = _Session(
        [
            _plugin(1, "https://github.com/acme/one", "kept"),
            _plugin(2, "https://github.com/acme/two", "   "),
        ]
    )
    monkeypatch.setattr(
        description_sync.http_helper,
        "get",
        _http_get(
            {
                "https://api.github.com/repos/acme/two/readme": (
                    True,
                    _readme("# Two"),
                    None,
                )
            }
        ),
    )

    result = await sync_market_plugin_descriptions(session, overwrite=False)

    actions = {item.plugin_id: item.action for item in result.items}
    assert actions == {1: "skipped", 2: "updated"}
    assert session.added[0].id == 2


@pytest.mark.asyncio
async def test_sync_reports_invalid_repository_url(monkeypatch):
    session = _Session([_plugin(1, "https://example.com/acme/one")])
    monkeypatch.setattr(description_sync.http_helper, "get", _http_get({}))

    result = await sync_market_plugin_descriptions(session)

    assert result.failed == 1
    assert result.items[0].message == "Invalid GitHub repository URL format"


@pytest.mark.asyncio
async def test_sync_caps_each_request_and_reports_remaining(monkeypatch):
    monkeypatch.setattr(description_sync, "MAX_DESCRIPTION_SYNC_PLUGINS", 1)
    session = _Session(
        [
            _plugin(1, "https://github.com/acme/one"),
            _plugin(2, "https://github.com/acme/two"),
        ]
    )
    monkeypatch.setattr(
        description_sync.http_helper,
        "get",
        _http_get(
            {
                "https://api.github.com/repos/acme/one/readme": (
                    True,
                    _readme("# One"),
                    None,
                )
            }
        ),
    )

    result = await sync_market_plugin_descriptions(session)

    assert result.total == 1
    assert result.remaining == 1


@pytest.mark.asyncio
async def test_sync_filters_by_framework(monkeypatch):
    session = _Session([])
    monkeypatch.setattr(description_sync.http_helper, "get", _http_get({}))

    result = await sync_market_plugin_descriptions(
        session, framework=PluginFramework.SWIFTLY, plugin_ids=[5, 5, 6]
    )

    assert result.total == 0
    compiled = str(session.statements[0])
    assert "market_plugins.framework" in compiled
    assert "market_plugins.id IN" in compiled


def test_readme_decoder_rejects_broken_payload():
    assert description_sync.decode_readme("not base64 !!!") is None


@pytest.mark.asyncio
async def test_fetch_readme_rejects_non_dict_payload(monkeypatch):
    monkeypatch.setattr(
        description_sync.http_helper,
        "get",
        _http_get({"https://api.github.com/repos/acme/one/readme": (True, "not-json", None)}),
    )

    readme, error = await description_sync._fetch_readme(
        "https://github.com/acme/one", github_token=None, github_proxy=None
    )

    assert readme is None
    assert error is not None


def test_market_plugin_defaults_to_counterstrikesharp_section():
    assert _plugin(1, "https://github.com/acme/one").framework is (
        PluginFramework.COUNTERSTRIKESHARP
    )
