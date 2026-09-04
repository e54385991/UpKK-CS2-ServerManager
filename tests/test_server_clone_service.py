from types import SimpleNamespace

import pytest

from services.server_clone_service import (
    CloneConflictError,
    CloneSourceError,
    ServerCloneInput,
    build_clone_template,
    normalize_game_directory,
    prepare_clone_server,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _Result(self.rows)


def _server(**overrides):
    values = {
        "id": 7,
        "user_id": 1,
        "name": "bravo",
        "host": "10.0.0.8",
        "ssh_port": 22,
        "ssh_user": "steam",
        "ssh_password": "source-secret",
        "sudo_password": "source-sudo",
        "apt_mirror": "ustc",
        "game_port": 27015,
        "game_directory": "/home/steam/cs2",
        "server_name": "Bravo",
        "default_map": "de_mirage",
        "max_players": 16,
        "game_mode": "competitive",
        "game_type": "0",
        "session_manager": "tmux",
        "additional_parameters": "+sv_hibernate_when_empty 0",
        "use_panel_proxy": False,
        "github_proxy": "https://ghfast.top",
        "client_port": None,
        "tv_enable": False,
        "tv_port": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_template_generates_unique_name_directory_and_runtime_port():
    source = _server()
    occupied = _server(
        id=8,
        name="bravo (2)",
        game_port=27025,
        game_directory="/home/steam/cs2-2/",
    )
    db = _Db([source, occupied])

    template = await build_clone_template(db, source, 1)

    assert template.name == "bravo (3)"
    assert template.game_directory == "/home/steam/cs2-3"
    assert template.game_port == 27035
    assert template.source_game_directory == "/home/steam/cs2"
    assert template.source_game_port == 27015
    assert template.server_name == "bravo (3)"
    assert template.has_sudo_password is True


@pytest.mark.asyncio
async def test_template_avoids_client_and_enabled_tv_ports():
    source = _server()
    occupied = _server(
        id=8,
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2-3",
        client_port=27036,
        tv_enable=True,
        tv_port=27046,
    )

    template = await build_clone_template(_Db([source, occupied]), source, 1)

    assert template.game_port == 27055


@pytest.mark.asyncio
async def test_template_rejects_source_without_saved_ssh_password():
    source = _server(ssh_password=None)

    with pytest.raises(CloneSourceError, match="SSH password"):
        await build_clone_template(_Db([source]), source, 1)


@pytest.mark.asyncio
async def test_template_clips_long_source_directory_for_a_valid_default():
    source = _server(game_directory="/" + "a" * 499)

    template = await build_clone_template(_Db([source]), source, 1)

    assert len(template.game_directory) <= 500
    assert template.game_directory != template.source_game_directory


def test_normalize_game_directory_rejects_relative_and_root_paths():
    assert normalize_game_directory(" /home/steam/cs2/../cs2-2/") == "/home/steam/cs2-2"
    with pytest.raises(ValueError, match="absolute"):
        normalize_game_directory("home/steam/cs2")
    with pytest.raises(ValueError, match="root"):
        normalize_game_directory("/")
    with pytest.raises(ValueError, match="root"):
        normalize_game_directory("//")


@pytest.mark.asyncio
async def test_prepare_clone_rejects_normalized_duplicate_directory():
    source = _server()
    db = _Db([source])
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2/../cs2",
        description=None,
        server_name="Charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager=None,
        apt_mirror=None,
        sudo_password=None,
        rcon_password=None,
        steam_account_token=None,
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
    )
    with pytest.raises(CloneConflictError, match="game directory"):
        await prepare_clone_server(db, source, 1, values)


@pytest.mark.asyncio
async def test_prepare_clone_rejects_source_directory_even_for_another_owner():
    source = _server()
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2/",
        description=None,
        server_name="Charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager=None,
        apt_mirror=None,
        sudo_password=None,
        rcon_password=None,
        steam_account_token=None,
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
    )

    with pytest.raises(CloneConflictError, match="differ from the source"):
        await prepare_clone_server(_Db([]), source, 2, values)


@pytest.mark.asyncio
async def test_prepare_clone_rejects_conflicting_game_port():
    source = _server()
    occupied = _server(id=8, name="other", game_port=27025, game_directory="/home/steam/other")
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2-2",
        description=None,
        server_name="Charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager=None,
        apt_mirror=None,
        sudo_password=None,
        rcon_password=None,
        steam_account_token=None,
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
    )

    with pytest.raises(CloneConflictError, match="Game port"):
        await prepare_clone_server(_Db([source, occupied]), source, 1, values)


@pytest.mark.asyncio
async def test_prepare_clone_requires_saved_ssh_password():
    source = _server(ssh_password=None)
    with pytest.raises(CloneSourceError, match="SSH password"):
        await prepare_clone_server(
            _Db([source]),
            source,
            1,
            ServerCloneInput(
                name="charlie",
                game_port=27025,
                game_directory="/home/steam/cs2-2",
                description=None,
                server_name="Charlie",
                default_map="de_dust2",
                max_players=32,
                game_mode="competitive",
                game_type="0",
                session_manager=None,
                apt_mirror=None,
                sudo_password=None,
                rcon_password=None,
                steam_account_token=None,
                additional_parameters=None,
                captcha_token=None,
                captcha_code=None,
            ),
        )


@pytest.mark.asyncio
async def test_prepare_clone_allows_explicitly_clearing_additional_parameters():
    source = _server()
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2-2",
        description=None,
        server_name="Charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager=None,
        apt_mirror=None,
        sudo_password=None,
        rcon_password=None,
        steam_account_token=None,
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
        additional_parameters_override=True,
    )

    prepared = await prepare_clone_server(_Db([source]), source, 1, values)

    assert prepared.additional_parameters is None


@pytest.mark.asyncio
async def test_prepare_clone_reuses_source_ssh_and_sudo_and_keeps_new_secrets_distinct():
    source = _server()
    db = _Db([source])
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2-2",
        description=None,
        server_name="Charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager="screen",
        apt_mirror=None,
        sudo_password=None,
        rcon_password="new-rcon",
        steam_account_token="NewGSLT",
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
    )

    prepared = await prepare_clone_server(db, source, 1, values)

    assert prepared.ssh_password == "source-secret"
    assert prepared.sudo_password == "source-sudo"
    assert prepared.rcon_password == "new-rcon"
    assert prepared.steam_account_token == "NewGSLT"
    assert prepared.session_manager == "screen"
    assert prepared.apt_mirror == "ustc"
    assert prepared.github_proxy == "https://ghfast.top"
    assert prepared.additional_parameters == "+sv_hibernate_when_empty 0"


@pytest.mark.asyncio
async def test_prepare_clone_root_user_keeps_ssh_secret_and_allows_sudo_override():
    source = _server(ssh_user="root", sudo_password="source-root-secret")
    db = _Db([source])
    values = ServerCloneInput(
        name="charlie",
        game_port=27025,
        game_directory="/home/steam/cs2-2",
        description=None,
        server_name="charlie",
        default_map="de_dust2",
        max_players=32,
        game_mode="competitive",
        game_type="0",
        session_manager=None,
        apt_mirror=None,
        sudo_password="new-root-secret",
        rcon_password=None,
        steam_account_token=None,
        additional_parameters=None,
        captcha_token=None,
        captcha_code=None,
    )

    prepared = await prepare_clone_server(db, source, 1, values)

    assert prepared.ssh_user == "root"
    assert prepared.ssh_password == "source-secret"
    assert prepared.sudo_password == "new-root-secret"
