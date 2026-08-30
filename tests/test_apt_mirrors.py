"""Unit coverage for the Ubuntu/Debian apt mirror catalog (no live SSH)."""

from services.apt_mirrors import (
    APT_MIRROR_IDS,
    APT_SOURCES_PREVIOUS_DIR,
    apply_apt_mirror_command,
    is_apt_source_failure,
    mirror_order,
    normalize_apt_mirror,
    parse_os_release,
    render_classic_sources_list,
    render_deb822_sources,
    require_apt_mirror,
    switch_hint,
)


def test_catalog_includes_official_ustc_and_tsinghua_aliases():
    assert APT_MIRROR_IDS == ("official", "ustc", "tuna")
    assert normalize_apt_mirror("tsinghua") == "tuna"
    assert normalize_apt_mirror("THU") == "tuna"
    assert normalize_apt_mirror("USTC") == "ustc"
    assert normalize_apt_mirror("official") == "official"
    assert normalize_apt_mirror("") is None
    assert normalize_apt_mirror("steamcmd") is None
    assert require_apt_mirror("tsinghua") == "tuna"


def test_mirror_order_puts_preferred_first():
    assert mirror_order(None) == ("official", "ustc", "tuna")
    assert mirror_order("tuna") == ("tuna", "official", "ustc")
    assert mirror_order("tsinghua") == ("tuna", "official", "ustc")


def test_parse_os_release_ubuntu_and_debian():
    ubuntu = parse_os_release("NAME=Ubuntu\nID=ubuntu\nVERSION_ID=24.04\nVERSION_CODENAME=noble\n")
    assert ubuntu is not None
    assert ubuntu.is_ubuntu is True
    assert ubuntu.version_codename == "noble"

    debian = parse_os_release('ID=debian\nVERSION_CODENAME="bookworm"\nVERSION_ID="12"\n')
    assert debian is not None
    assert debian.is_debian is True
    assert debian.version_codename == "bookworm"

    mint = parse_os_release("ID=linuxmint\nID_LIKE=ubuntu\nVERSION_CODENAME=wilma\n")
    assert mint is not None
    assert mint.id == "ubuntu"

    assert parse_os_release("ID=fedora\nVERSION_CODENAME=forty\n") is None
    assert parse_os_release("ID=ubuntu\n") is None


def test_render_sources_use_well_known_urls():
    os_release = parse_os_release("ID=ubuntu\nVERSION_CODENAME=noble\nVERSION_ID=24.04\n")
    assert os_release is not None
    official = render_deb822_sources(os_release, "official")
    assert "http://archive.ubuntu.com/ubuntu" in official
    assert "http://security.ubuntu.com/ubuntu" in official
    assert "noble-security" in official
    assert "universe" in official

    ustc = render_classic_sources_list(os_release, "ustc")
    assert "https://mirrors.ustc.edu.cn/ubuntu" in ustc
    assert "noble-updates" in ustc

    tuna = render_deb822_sources(os_release, "tsinghua")
    assert "https://mirrors.tuna.tsinghua.edu.cn/ubuntu" in tuna

    debian = parse_os_release("ID=debian\nVERSION_CODENAME=bookworm\n")
    assert debian is not None
    debian_sources = render_deb822_sources(debian, "tuna")
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian" in debian_sources
    assert "debian-security" in debian_sources
    assert "non-free-firmware" in debian_sources


def test_apply_command_backs_up_and_rewrites_deb822():
    os_release = parse_os_release("ID=ubuntu\nVERSION_CODENAME=noble\nVERSION_ID=24.04\n")
    assert os_release is not None
    command = apply_apt_mirror_command(os_release, "ustc")
    assert APT_SOURCES_PREVIOUS_DIR in command
    assert "ubuntu.sources" in command
    assert "https://mirrors.ustc.edu.cn/ubuntu" in command
    assert "/etc/apt/sources.list" in command
    assert "official-package-repositories.list" in command
    assert "applied:" in command
    assert "set -eu" in command


def test_source_failure_detection_and_switch_hint():
    assert is_apt_source_failure("", "E: Failed to fetch http://archive.ubuntu.com")
    assert is_apt_source_failure("Could not resolve 'mirrors.example'")
    assert not is_apt_source_failure("", "E: Unable to locate package lib32z1")
    hint = switch_hint(("official",), "ustc")
    assert "Official" in hint
    assert "USTC" in hint
    assert "operations center" in hint
