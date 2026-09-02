"""SSH plugin operations facade and compatibility exports."""

# ruff: noqa: F403,F405

from .common import *
from .plugin_backup import PluginBackupMixin
from .plugin_counterstrikesharp import CounterStrikeSharpMixin
from .plugin_cs2fixes import CS2FixesMixin
from .plugin_metamod import MetamodMixin
from .plugin_swiftly import SwiftlyMixin


class PluginOperationsMixin(
    MetamodMixin,
    CounterStrikeSharpMixin,
    CS2FixesMixin,
    SwiftlyMixin,
    PluginBackupMixin,
):
    """Composed plugin capabilities kept at the legacy import path."""

    pass
