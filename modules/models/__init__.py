"""Compatibility facade for domain-split models."""

# ruff: noqa: F401,F403

from .ai import (
    AIConversation,
    AIMessage,
    AIRun,
    AISystemSettings,
    AIToolRun,
    UserAISettings,
)
from .common import *
from .identity import (
    PasswordResetToken,
    User,
)
from .plugins import (
    GitHubInstallRecipe,
    ManagedPlugin,
    ManagedPluginFile,
    MarketPlugin,
    PluginCategory,
    PluginConfigSource,
    PluginConflictRule,
    PluginDiagnosticRun,
    PluginDiagnosticStep,
    PluginQuarantineEntry,
)
from .servers import (
    AuthType,
    CustomCommand,
    DeploymentLog,
    InitializedServer,
    MonitoringLog,
    ScheduledTask,
    Server,
    ServerStatus,
)
from .system import (
    SSHServerSudo,
    SystemSettings,
)

# Preserve annotations, pickles and integrations which identify these classes
# by the historical ``modules.models`` module path.
for _value in tuple(globals().values()):
    if isinstance(_value, type) and _value.__module__.startswith(f"{__name__}."):
        _value.__module__ = __name__

del _value
