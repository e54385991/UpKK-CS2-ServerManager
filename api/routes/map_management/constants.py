"""Stable MapChooser route constants shared by the facade and request models."""

from __future__ import annotations

PLUGIN_CENTER_NAME = "CS2-Upkk-PanelPLG-Mapchooser"
PLUGIN_CENTER_URL = "/plugin-market?search=CS2-Upkk-PanelPLG-Mapchooser"
MAP_PRESET_URLS = {
    "kz": (
        "https://raw.githubusercontent.com/UpKK-Xnet-YYDCS/GeneralMapcfg_Public/"
        "refs/heads/master/cs2/kz/counterstrikesharp/configs/plugins/MapChooser/maps.txt"
    ),
    "ze": (
        "https://raw.githubusercontent.com/UpKK-Xnet-YYDCS/UPKK_ZE_PUBLIC/"
        "refs/heads/master/cs2/counterstrikesharp/configs/plugins/MapChooser/maps.txt"
    ),
}
KZ_PLUGIN_CONFIG = {
    "UseGameTimeLimit": False,
    "EnforceTimeLimit": True,
    "ChangeMapUse_host_workshop_map": True,
}
MAP_POOL_SYNC_ACTION = "map_pool_sync"
MAP_POOL_SYNC_TASK_NAME = "MapChooser custom map-pool sync"
MAP_POOL_SYNC_MIN_INTERVAL_SECONDS = 300
MAPCHOOSER_UNINSTALL_CONFIRMATION = "UNINSTALL MAPCHOOSER"
