"""Focused tests for MapChooser map-list management."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.routes import map_management
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MapConfigError,
    PluginConfigError,
    append_map_to_config,
    build_plugin_config_fields,
    content_revision,
    normalize_workshop_id,
    parse_maps_config,
    parse_plugin_config,
    remove_map_from_config,
    render_official_maps_config,
    sanitize_map_name,
    set_map_enabled,
    update_plugin_config,
    validate_restricted_times,
)

SAMPLE_CONFIG = """// Keep this comment
"Maplist"
{
    "Dust Workshop"
    {
        "workshop_id" "3070591565"
        "enabled" "1"
        "filename" "de_dust2_classic"
        "updatedname" "Dust Workshop"
        "MinPlayers" "6"
        "OnlyNominate" "1"
        "RestrictedTimes" "01:00-08:00;18:00-20:00"
        "future_field" "preserve me"
    }
}
"""

SAMPLE_PLUGIN_CONFIG = """{
  "AllowExtend": true,
  "RTVPercent": 0.6,
  "FutureScalar": "keep and edit",
  "FutureNested": {"enabled": true}
}
"""


class MapConfigTests(unittest.TestCase):
    def test_parser_reads_mapchooser_fields(self):
        parsed = parse_maps_config(SAMPLE_CONFIG)

        self.assertEqual(len(parsed.maps), 1)
        item = parsed.maps[0]
        self.assertEqual(item["name"], "Dust Workshop")
        self.assertEqual(item["workshop_id"], "3070591565")
        self.assertEqual(item["filename"], "de_dust2_classic")
        self.assertEqual(item["min_players"], "6")
        self.assertTrue(item["only_nominate"])
        self.assertEqual(item["restricted_times"], "01:00-08:00;18:00-20:00")

    def test_quick_add_preserves_comments_and_unknown_fields(self):
        updated = append_map_to_config(
            SAMPLE_CONFIG,
            name="New Map",
            workshop_id="3298427415",
            min_players=4,
            restricted_times="02:00-07:30",
        )

        self.assertIn("// Keep this comment", updated)
        self.assertIn('"future_field" "preserve me"', updated)
        self.assertIn('"workshop_id"\t"3298427415"', updated)
        parsed = parse_maps_config(updated)
        self.assertEqual(
            [item["workshop_id"] for item in parsed.maps], ["3070591565", "3298427415"]
        )
        self.assertEqual(parsed.maps[1]["min_players"], "4")

    def test_quick_add_rejects_duplicate_id_and_name(self):
        with self.assertRaisesRegex(MapConfigError, "already exists"):
            append_map_to_config(
                SAMPLE_CONFIG,
                name="Other Name",
                workshop_id="3070591565",
            )
        with self.assertRaisesRegex(MapConfigError, "already exists"):
            append_map_to_config(
                SAMPLE_CONFIG,
                name="dust workshop",
                workshop_id="3298427415",
            )

    def test_official_map_config_is_derived_without_user_supplied_fields(self):
        content = render_official_maps_config(["de_dust2", "KZ_HUB", "DE_DUST2"])

        parsed = parse_maps_config(content)
        self.assertEqual([item["name"] for item in parsed.maps], ["de_dust2", "KZ_HUB"])
        self.assertTrue(all(item["workshop_id"] == "" for item in parsed.maps))
        self.assertEqual([item["filename"] for item in parsed.maps], ["de_dust2", "KZ_HUB"])
        self.assertEqual([item["updated_name"] for item in parsed.maps], ["de_dust2", "KZ_HUB"])
        self.assertNotIn('"workshop_id"', content)
        self.assertIn('"enabled"\t"1"', content)
        self.assertIn('"filename"\t"KZ_HUB"', content)
        self.assertIn('"updatedname"\t"KZ_HUB"', content)

    def test_parser_rejects_invalid_root_invalid_id_and_duplicate_id(self):
        invalid_documents = (
            '"NotMaplist"\n{\n}\n',
            '"Maplist"\n{\n"Invalid ID" { "workshop_id" "abc" }\n}\n',
            ('"Maplist"\n{\n"A" { "workshop_id" "123456" }\n"B" { "workshop_id" "123456" }\n}\n'),
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(MapConfigError):
                parse_maps_config(document)

    def test_default_config_is_valid_and_revision_is_stable(self):
        self.assertEqual(parse_maps_config(DEFAULT_MAPS_CONFIG).maps, [])
        self.assertEqual(
            content_revision(DEFAULT_MAPS_CONFIG), content_revision(DEFAULT_MAPS_CONFIG)
        )
        self.assertNotEqual(
            content_revision(DEFAULT_MAPS_CONFIG), content_revision(DEFAULT_MAPS_CONFIG + "\n")
        )

    def test_workshop_id_accepts_numeric_id_and_steam_url_only(self):
        self.assertEqual(normalize_workshop_id("3070591565"), "3070591565")
        self.assertEqual(
            normalize_workshop_id(
                "https://steamcommunity.com/sharedfiles/filedetails/?id=3070591565&searchtext=test"
            ),
            "3070591565",
        )
        for invalid in ("", "123", "0" * 10, "https://example.com/?id=3070591565"):
            with self.subTest(invalid=invalid), self.assertRaises(MapConfigError):
                normalize_workshop_id(invalid)

    def test_names_and_restricted_times_are_safe_for_reference_parser(self):
        self.assertEqual(
            sanitize_map_name('  Map "Quoted"\\Folder\nName  '), "Map 'Quoted'/Folder Name"
        )
        self.assertEqual(
            validate_restricted_times("01:00-08:00; 18:00-20:00"),
            "01:00-08:00;18:00-20:00",
        )
        with self.assertRaises(MapConfigError):
            validate_restricted_times("25:00-27:00")

    def test_map_can_be_disabled_and_reenabled_in_place(self):
        disabled = set_map_enabled(
            SAMPLE_CONFIG,
            name="Dust Workshop",
            workshop_id="3070591565",
            enabled=False,
        )
        self.assertFalse(parse_maps_config(disabled).maps[0]["enabled"])
        self.assertIn("// Keep this comment", disabled)
        self.assertIn('"future_field" "preserve me"', disabled)

        enabled = set_map_enabled(
            disabled,
            name="Dust Workshop",
            workshop_id="3070591565",
            enabled=True,
        )
        self.assertTrue(parse_maps_config(enabled).maps[0]["enabled"])

    def test_disabling_map_adds_missing_enabled_field(self):
        content = '"Maplist"\n{\n\t"Official"\n\t{\n\t\t"workshop_id" "0"\n\t}\n}\n'
        updated = set_map_enabled(
            content,
            name="Official",
            workshop_id="0",
            enabled=False,
        )
        self.assertIn('"enabled"\t"0"', updated)
        self.assertFalse(parse_maps_config(updated).maps[0]["enabled"])

    def test_map_can_be_removed_without_rebuilding_other_entries(self):
        content = append_map_to_config(
            SAMPLE_CONFIG,
            name="Keep This Map",
            workshop_id="3298427415",
        )
        updated = remove_map_from_config(
            content,
            name="Dust Workshop",
            workshop_id="3070591565",
        )
        parsed = parse_maps_config(updated)
        self.assertEqual([item["name"] for item in parsed.maps], ["Keep This Map"])
        self.assertIn("// Keep this comment", updated)
        self.assertNotIn('"future_field" "preserve me"', updated)

    def test_map_mutations_reject_missing_identity(self):
        for callback in (set_map_enabled, remove_map_from_config):
            kwargs = {
                "content": SAMPLE_CONFIG,
                "name": "Missing Map",
                "workshop_id": "3298427415",
            }
            if callback is set_map_enabled:
                kwargs["enabled"] = False
            with (
                self.subTest(callback=callback.__name__),
                self.assertRaisesRegex(
                    MapConfigError,
                    "was not found",
                ),
            ):
                callback(**kwargs)


class PluginConfigTests(unittest.TestCase):
    def test_default_plugin_config_generates_visual_fields(self):
        config = parse_plugin_config(DEFAULT_PLUGIN_CONFIG_CONTENT)
        fields, unsupported = build_plugin_config_fields(config)

        self.assertEqual(unsupported, [])
        self.assertEqual(fields[0]["key"], "VoteStartTime")
        self.assertEqual(fields[0]["kind"], "number")
        allow_extend = next(field for field in fields if field["key"] == "AllowExtend")
        self.assertEqual(allow_extend["kind"], "boolean")
        self.assertTrue(allow_extend["known"])

    def test_utf8_bom_is_accepted_and_removed_when_saved(self):
        content = "\ufeff" + SAMPLE_PLUGIN_CONFIG

        parsed = parse_plugin_config(content)
        updated = update_plugin_config(content, {"AllowExtend": False})

        self.assertTrue(parsed["AllowExtend"])
        self.assertFalse(updated.startswith("\ufeff"))
        self.assertFalse(json.loads(updated)["AllowExtend"])

    def test_jsonc_comments_and_trailing_commas_are_supported(self):
        content = r"""{
  // A line comment
  "AllowExtend": true,
  "VoteStartSound": "https://example.com/audio/*keep*/.mp3", /* block comment */
  "FutureNested": {
    "enabled": true,
  },
}"""

        parsed = parse_plugin_config(content)
        updated = update_plugin_config(content, {"AllowExtend": False})

        self.assertTrue(parsed["AllowExtend"])
        self.assertEqual(
            parsed["VoteStartSound"],
            "https://example.com/audio/*keep*/.mp3",
        )
        self.assertEqual(parsed["FutureNested"], {"enabled": True})
        self.assertFalse(json.loads(updated)["AllowExtend"])

    def test_jsonc_rejects_unterminated_block_comment(self):
        with self.assertRaisesRegex(PluginConfigError, "Unterminated JSONC block comment"):
            parse_plugin_config('{"AllowExtend": true /* missing end')

    def test_visual_update_preserves_unknown_and_nested_settings(self):
        updated = update_plugin_config(
            SAMPLE_PLUGIN_CONFIG,
            {
                "AllowExtend": False,
                "RTVPercent": 0.75,
                "FutureScalar": "changed",
            },
        )
        parsed = json.loads(updated)

        self.assertFalse(parsed["AllowExtend"])
        self.assertEqual(parsed["RTVPercent"], 0.75)
        self.assertEqual(parsed["FutureScalar"], "changed")
        self.assertEqual(parsed["FutureNested"], {"enabled": True})
        fields, unsupported = build_plugin_config_fields(parsed)
        self.assertFalse(next(field for field in fields if field["key"] == "FutureScalar")["known"])
        self.assertEqual(unsupported, ["FutureNested"])

    def test_visual_update_rejects_invalid_values_and_unknown_keys(self):
        invalid_updates = (
            {"AllowExtend": "false"},
            {"RTVPercent": 1.5},
            {"FutureNested": {}},
            {"Missing": True},
        )
        for values in invalid_updates:
            with self.subTest(values=values), self.assertRaises(PluginConfigError):
                update_plugin_config(SAMPLE_PLUGIN_CONFIG, values)

    def test_preset_update_can_add_missing_known_fields(self):
        updated = update_plugin_config(
            '{"AllowExtend": true}\n',
            {
                "UseGameTimeLimit": False,
                "EnforceTimeLimit": True,
                "ChangeMapUse_host_workshop_map": True,
            },
            allow_missing_known_fields=True,
        )

        parsed = json.loads(updated)
        self.assertTrue(parsed["AllowExtend"])
        self.assertFalse(parsed["UseGameTimeLimit"])
        self.assertTrue(parsed["EnforceTimeLimit"])
        self.assertTrue(parsed["ChangeMapUse_host_workshop_map"])


class FakeSSHManager:
    def __init__(self, *, status_output: str, content: str = DEFAULT_MAPS_CONFIG):
        self.status_output = status_output
        self.content = content
        self.commands: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.disconnected = False

    async def connect(self, server):
        return True, ""

    async def execute_command(self, command, timeout=30):
        self.commands.append(command)
        if "printf 'counterstrikesharp" in command:
            return True, self.status_output, ""
        return True, "", ""

    async def read_file(self, path, server, max_size):
        return True, self.content, ""

    async def write_file(self, path, content, server):
        self.writes.append((path, content))
        return True, ""

    async def disconnect(self):
        self.disconnected = True


class PresetSSHManager(FakeSSHManager):
    def __init__(
        self,
        *,
        maps_content: str = DEFAULT_MAPS_CONFIG,
        plugin_content: str = DEFAULT_PLUGIN_CONFIG_CONTENT,
        official_files: str = "",
    ):
        super().__init__(
            status_output=("counterstrikesharp=1\nmapchooser=1\nmaps_file=1\nconfig_file=1\n"),
            content=maps_content,
        )
        self.maps_content = maps_content
        self.plugin_content = plugin_content
        self.official_files = official_files

    async def execute_command(self, command, timeout=30):
        self.commands.append(command)
        if "printf 'counterstrikesharp" in command:
            return True, self.status_output, ""
        if "-name '*.vpk'" in command:
            return True, self.official_files, ""
        return True, "", ""

    async def read_file(self, path, server, max_size):
        if path.endswith("/config.json"):
            return True, self.plugin_content, ""
        return True, self.maps_content, ""


class FakeScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class FakeDatabase:
    def __init__(self, results):
        self.results = iter(results)
        self.add = MagicMock()
        self.commits = 0

    async def execute(self, statement):
        return FakeScalarResult(next(self.results))

    async def commit(self):
        self.commits += 1

    async def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 99


class MapRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = SimpleNamespace(
            id=12,
            game_directory="/home/cs2/server one",
            map_pool_sync_url=None,
        )
        self.user = SimpleNamespace(id=7, is_admin=False)
        map_management._map_write_locks.clear()

    def test_status_reports_missing_mapchooser_and_install_target(self):
        ssh = FakeSSHManager(status_output="counterstrikesharp=1\nmapchooser=0\nmaps_file=0\n")
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.get_map_management_status(12, db=object(), current_user=self.user)
            )

        self.assertTrue(result["counterstrikesharp_installed"])
        self.assertFalse(result["mapchooser_installed"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["plugin_center_name"], "CS2-Upkk-PanelPLG-Mapchooser")
        self.assertEqual(
            result["plugin_center_url"],
            "/plugin-market?search=CS2-Upkk-PanelPLG-Mapchooser",
        )
        self.assertTrue(ssh.disconnected)
        self.assertIn("'", ssh.commands[0])
        self.assertIn("CounterStrikeSharp.API.dll", ssh.commands[0])

    def test_get_config_enforces_prerequisites(self):
        ssh = FakeSSHManager(status_output="counterstrikesharp=0\nmapchooser=0\nmaps_file=0\n")
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            self.assertRaises(HTTPException) as caught,
        ):
            asyncio.run(map_management.get_maps_config(12, db=object(), current_user=self.user))

        self.assertEqual(caught.exception.status_code, 412)
        self.assertEqual(
            caught.exception.detail["missing"],
            ["counterstrikesharp", "mapchooser"],
        )
        self.assertTrue(ssh.disconnected)

    def test_invalid_existing_config_is_returned_for_manual_repair(self):
        invalid_content = '"Maplist"\n{\n"Broken" { "workshop_id" "invalid" }\n}\n'
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
            content=invalid_content,
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.get_maps_config(12, db=object(), current_user=self.user)
            )

        self.assertEqual(result["content"], invalid_content)
        self.assertEqual(result["maps"], [])
        self.assertIn("Invalid maps.txt", result["config_error"])

    def test_official_preset_scans_vpks_and_derives_plugin_fields(self):
        ssh = PresetSSHManager(official_files="de_dust2.vpk\ncs_office.vpk\n")
        request = map_management.MapPresetApplyRequest(
            preset="official",
            expected_revision=content_revision(DEFAULT_MAPS_CONFIG),
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.apply_map_preset(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        self.assertEqual(result["map_count"], 2)
        self.assertEqual([item["name"] for item in result["maps"]], ["cs_office", "de_dust2"])
        written_maps = ssh.writes[0][1]
        self.assertNotIn('"workshop_id"', written_maps)
        self.assertIn('"enabled"\t"1"', written_maps)
        self.assertIn('"filename"\t"de_dust2"', written_maps)
        self.assertIn('"updatedname"\t"de_dust2"', written_maps)

    def test_kz_preset_replaces_pool_and_updates_required_plugin_settings(self):
        kz_content = append_map_to_config(
            DEFAULT_MAPS_CONFIG,
            name="kz_example",
            workshop_id="3070591565",
        )
        plugin_content = json.dumps(
            {
                "AllowExtend": True,
                "UseGameTimeLimit": True,
                "EnforceTimeLimit": False,
                "ChangeMapUse_host_workshop_map": False,
            }
        )
        ssh = PresetSSHManager(plugin_content=plugin_content)
        request = map_management.MapPresetApplyRequest(
            preset="kz",
            expected_revision=content_revision(DEFAULT_MAPS_CONFIG),
            plugin_config_expected_revision=content_revision(plugin_content),
        )
        fetch_preset = AsyncMock(return_value=kz_content)
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            patch.object(map_management, "_remote_maps_config", new=fetch_preset),
        ):
            result = asyncio.run(
                map_management.apply_map_preset(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        fetch_preset.assert_awaited_once_with("kz")
        self.assertEqual(result["map_count"], 1)
        self.assertEqual(len(ssh.writes), 2)
        written_plugin = json.loads(ssh.writes[0][1])
        self.assertTrue(written_plugin["AllowExtend"])
        self.assertFalse(written_plugin["UseGameTimeLimit"])
        self.assertTrue(written_plugin["EnforceTimeLimit"])
        self.assertTrue(written_plugin["ChangeMapUse_host_workshop_map"])
        self.assertEqual(ssh.writes[1][1], kz_content)

    def test_custom_sync_settings_enforce_public_url_and_create_internal_task(self):
        db = FakeDatabase([[]])
        request = map_management.CustomMapSyncUpdateRequest(
            url="https://maps.example.com/maps.txt",
            interval_seconds=300,
            enabled=True,
        )
        with (
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            patch.object(
                map_management,
                "validate_remote_map_url",
                new=AsyncMock(return_value=request.url),
            ),
        ):
            result = asyncio.run(
                map_management.update_custom_map_sync(
                    12,
                    request,
                    db=db,
                    current_user=self.user,
                )
            )

        self.assertTrue(result["enabled"])
        self.assertEqual(result["interval_seconds"], 300)
        self.assertEqual(self.server.map_pool_sync_url, request.url)
        task = next(
            call.args[0]
            for call in db.add.call_args_list
            if getattr(call.args[0], "action", None) == map_management.MAP_POOL_SYNC_ACTION
        )
        self.assertEqual(task.schedule_value, "300")
        self.assertTrue(task.enabled)

    def test_custom_sync_run_replaces_maps_immediately(self):
        remote_content = append_map_to_config(
            DEFAULT_MAPS_CONFIG,
            name="Remote Map",
            workshop_id="3298427415",
        )
        self.server.map_pool_sync_url = "https://maps.example.com/maps.txt"
        db = FakeDatabase([[]])
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
        )
        request = map_management.CustomMapSyncRunRequest(
            expected_revision=content_revision(DEFAULT_MAPS_CONFIG)
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            patch.object(
                map_management,
                "fetch_remote_map_pool",
                new=AsyncMock(return_value=remote_content),
            ),
        ):
            result = asyncio.run(
                map_management.run_custom_map_sync(
                    12,
                    request,
                    db=db,
                    current_user=self.user,
                )
            )

        self.assertEqual(result["map_count"], 1)
        self.assertEqual(ssh.writes[0][1], remote_content)

    def test_uninstall_removes_only_exact_mapchooser_plugin_directory(self):
        db = FakeDatabase([[], []])
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
        )
        request = map_management.MapChooserUninstallRequest(
            confirmation=map_management.MAPCHOOSER_UNINSTALL_CONFIRMATION
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.uninstall_mapchooser_plugin(
                    12,
                    request,
                    db=db,
                    current_user=self.user,
                )
            )

        expected_path = (
            "/home/cs2/server one/cs2/game/csgo/addons/counterstrikesharp/plugins/MapChooser"
        )
        self.assertEqual(result["deleted_path"], expected_path)
        delete_command = next(command for command in ssh.commands if "rm -rf --" in command)
        self.assertIn("plugins/MapChooser", delete_command)
        self.assertNotIn("configs/plugins/MapChooser", delete_command)
        self.assertFalse(result["ready"])

    def test_update_rejects_stale_revision_without_writing(self):
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
            content=SAMPLE_CONFIG,
        )
        request = map_management.MapConfigUpdateRequest(
            content=DEFAULT_MAPS_CONFIG,
            expected_revision="0" * 64,
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            self.assertRaises(HTTPException) as caught,
        ):
            asyncio.run(
                map_management.update_maps_config(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(ssh.writes, [])

    def test_add_map_writes_temp_file_then_atomically_replaces(self):
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
            content=DEFAULT_MAPS_CONFIG,
        )
        request = map_management.MapAddRequest(
            workshop_id="3298427415",
            name="New Map",
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.add_map(12, request, db=object(), current_user=self.user)
            )

        self.assertEqual(result["added_map"], {"name": "New Map", "workshop_id": "3298427415"})
        self.assertEqual(len(ssh.writes), 1)
        self.assertIn('"workshop_id"\t"3298427415"', ssh.writes[0][1])
        self.assertTrue(any(command.startswith("mv -f -- ") for command in ssh.commands))

    def test_add_map_fetches_workshop_title_when_name_is_omitted(self):
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=0\n",
        )
        request = map_management.MapAddRequest(workshop_id="3298427415")
        title_lookup = AsyncMock(return_value="Steam Map Title")
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
            patch.object(map_management, "_fetch_workshop_title", new=title_lookup),
        ):
            result = asyncio.run(
                map_management.add_map(12, request, db=object(), current_user=self.user)
            )

        title_lookup.assert_awaited_once_with("3298427415")
        self.assertEqual(result["added_map"]["name"], "Steam Map Title")
        self.assertIn('"Steam Map Title"', result["content"])

    def test_route_can_disable_map(self):
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
            content=SAMPLE_CONFIG,
        )
        request = map_management.MapEnabledUpdateRequest(
            name="Dust Workshop",
            workshop_id="3070591565",
            expected_revision=content_revision(SAMPLE_CONFIG),
            enabled=False,
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.update_map_enabled(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        self.assertFalse(result["maps"][0]["enabled"])
        self.assertIn('"enabled" "0"', ssh.writes[0][1])

    def test_route_can_delete_map_pool_entry(self):
        ssh = FakeSSHManager(
            status_output="counterstrikesharp=1\nmapchooser=1\nmaps_file=1\n",
            content=SAMPLE_CONFIG,
        )
        request = map_management.MapIdentityRequest(
            name="Dust Workshop",
            workshop_id="3070591565",
            expected_revision=content_revision(SAMPLE_CONFIG),
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.delete_map(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        self.assertEqual(result["maps"], [])
        self.assertNotIn("Dust Workshop", ssh.writes[0][1])
        self.assertIn("Maplist", ssh.writes[0][1])

    def test_get_plugin_config_returns_dynamic_visual_fields(self):
        ssh = FakeSSHManager(
            status_output=("counterstrikesharp=1\nmapchooser=1\nmaps_file=1\nconfig_file=1\n"),
            content=SAMPLE_PLUGIN_CONFIG,
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.get_plugin_config(12, db=object(), current_user=self.user)
            )

        self.assertTrue(result["plugin_config_file_exists"])
        self.assertEqual(
            [field["key"] for field in result["fields"]],
            ["AllowExtend", "RTVPercent", "FutureScalar"],
        )
        self.assertEqual(result["unsupported_fields"], ["FutureNested"])

    def test_update_plugin_config_checks_revision_and_atomically_writes(self):
        ssh = FakeSSHManager(
            status_output=("counterstrikesharp=1\nmapchooser=1\nmaps_file=1\nconfig_file=1\n"),
            content=SAMPLE_PLUGIN_CONFIG,
        )
        request = map_management.PluginConfigUpdateRequest(
            values={"AllowExtend": False, "RTVPercent": 0.7},
            expected_revision=content_revision(SAMPLE_PLUGIN_CONFIG),
        )
        with (
            patch.object(map_management, "SSHManager", return_value=ssh),
            patch.object(
                map_management,
                "get_server_with_permission",
                new=AsyncMock(return_value=self.server),
            ),
        ):
            result = asyncio.run(
                map_management.update_mapchooser_plugin_config(
                    12,
                    request,
                    db=object(),
                    current_user=self.user,
                )
            )

        saved = json.loads(ssh.writes[0][1])
        self.assertFalse(saved["AllowExtend"])
        self.assertEqual(saved["RTVPercent"], 0.7)
        self.assertEqual(saved["FutureNested"], {"enabled": True})
        self.assertTrue(result["plugin_config_file_exists"])
        self.assertTrue(any(command.startswith("mv -f -- ") for command in ssh.commands))


if __name__ == "__main__":
    unittest.main()
