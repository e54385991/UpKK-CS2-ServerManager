/**
 * Server status as reported by the backend. Kept as a string-literal union so
 * the UI can map each state to a status tone and label deterministically.
 */
export type ServerStatus =
  | "pending"
  | "deploying"
  | "running"
  | "stopped"
  | "error"
  | "unknown";

/**
 * Non-secret projection of a server used across list and card views. Secret
 * fields (SSH/RCON/GSLT/API keys) are intentionally excluded — the console
 * never receives them in summaries.
 */
export type ServerSummary = {
  readonly id: number;
  readonly name: string;
  readonly host: string;
  readonly gamePort: number;
  readonly sshUser: string;
  readonly status: ServerStatus;
  readonly description: string | null;
  readonly defaultMap: string;
  readonly maxPlayers: number;
  readonly ownerId: number | null;
  readonly ownerUsername: string | null;
  readonly ownerIsAdmin: boolean | null;
  readonly usePanelProxy: boolean;
  readonly githubProxy: string | null;
  readonly isSshDown: boolean;
  readonly sshHealthStatus: string;
  readonly consecutiveSshFailures: number;
  readonly sshHealthFailureThreshold: number;
  readonly sshHealthCheckIntervalHours: number;
  readonly lastSshHealthCheck: string | null;
};

export type ServerProxyMode = "panel" | "github_url" | "direct";

export function serverProxyMode(server: {
  readonly usePanelProxy: boolean;
  readonly githubProxy: string | null;
}): ServerProxyMode {
  if (server.usePanelProxy) return "panel";
  if (server.githubProxy) return "github_url";
  return "direct";
}

export type ServerListScope = "mine" | "all";

export type SteamLatestVersion = {
  readonly available: boolean;
  readonly version: string | null;
  readonly message: string | null;
  readonly timestamp: string | null;
};

export type DiskSpace = {
  readonly serverId: number;
  readonly cached: boolean;
  readonly usedGb: number | null;
  readonly totalGb: number | null;
  readonly availableGb: number | null;
  readonly usedPercent: number | null;
};

export type HostSystemInfo = {
  readonly serverId: number;
  readonly cached: boolean;
  readonly success: boolean;
  readonly systemType: string | null;
  readonly architecture: string | null;
  readonly cpuModel: string | null;
  readonly cpuCores: number | null;
  readonly kernelVersion: string | null;
  readonly distribution: string | null;
  readonly distributionVersion: string | null;
  readonly distributionPrettyName: string | null;
  readonly memoryTotalBytes: number | null;
  readonly memoryAvailableBytes: number | null;
  readonly collectedAt: string | null;
};

export type A2SServerInfo = {
  readonly serverName: string | null;
  readonly mapName: string | null;
  readonly folder: string | null;
  readonly game: string | null;
  readonly playerCount: number | null;
  readonly maxPlayers: number | null;
  readonly botCount: number | null;
  readonly serverType: string | null;
  readonly platform: string | null;
  readonly passwordProtected: boolean | null;
  readonly vacEnabled: boolean | null;
  readonly version: string | null;
  readonly ping: number | null;
  readonly keywords: string | null;
};

export type A2SPlayer = {
  readonly name: string;
  readonly score: number;
  readonly duration: number;
};

export type A2SQuery = {
  readonly queryHost: string;
  readonly queryPort: number;
  readonly success: boolean;
  readonly cached: boolean;
  readonly live: boolean;
  readonly serverInfo: A2SServerInfo | null;
  readonly players: readonly A2SPlayer[];
  readonly timestamp: string | null;
  readonly lastUpdated: string | null;
  readonly responseTimeMs: number | null;
  readonly error: string | null;
};

export type MonitoringLog = {
  readonly id: string;
  readonly eventType: string;
  readonly status: string;
  readonly message: string;
  readonly createdAt: string | null;
};

export type A2SCache = {
  readonly serverId: number;
  readonly cached: boolean;
  readonly success: boolean | null;
  readonly playerCount: number | null;
  readonly maxPlayers: number | null;
  readonly mapName: string | null;
  readonly serverName: string | null;
  readonly version: string | null;
  readonly lastUpdated: string | null;
  readonly responseTimeMs: number | null;
};

export const BATCH_ACTIONS = ["restart", "stop", "update"] as const;
export type BatchAction = (typeof BATCH_ACTIONS)[number];

export const BATCH_PLUGINS = [
  "metamod",
  "counterstrikesharp",
  "cs2fixes",
] as const;
export type BatchPlugin = (typeof BATCH_PLUGINS)[number];

export type BatchServerStatus = {
  readonly serverId: number;
  readonly status: string;
  readonly message: string;
};

export type BatchSummary = {
  readonly total: number;
  readonly completed: number;
  readonly succeeded: number;
  readonly failed: number;
  readonly inProgress: number;
  readonly isComplete: boolean;
};

export type BatchActionAccepted = {
  readonly batchId: string;
  readonly action: string;
  readonly serverCount: number;
  readonly acceptedServerIds: readonly number[];
  readonly streamUrl: string;
  readonly message: string;
};

export type BatchJournal = {
  readonly batchId: string;
  readonly action: string | null;
  readonly servers: readonly BatchServerStatus[];
  readonly summary: BatchSummary;
};

export type S3BackupItem = {
  readonly key: string;
  readonly filename: string;
  readonly size: number;
  readonly lastModified: string | null;
};

export type S3BackupList = {
  readonly configured: boolean;
  readonly items: readonly S3BackupItem[];
  readonly message: string | null;
};

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/**
 * Status → visual tone. Human labels are resolved via i18n at render time
 * (`servers.status.<status>`), so this map holds presentation only.
 */
export const SERVER_STATUS_TONE: Record<ServerStatus, Tone> = {
  running: "ok",
  deploying: "info",
  pending: "warn",
  stopped: "neutral",
  error: "danger",
  unknown: "neutral",
};

export const SERVER_OPERATION_ACTIONS = [
  "deploy",
  "start",
  "stop",
  "restart",
  "status",
  "update",
  "validate",
  "install_metamod",
  "install_counterstrikesharp",
  "install_cs2fixes",
  "install_swiftly",
  "update_metamod",
  "update_counterstrikesharp",
  "update_cs2fixes",
  "update_swiftly",
  "backup_plugins",
  "install_plugin",
  "install_github_plugin",
  "uninstall_github_plugin",
  "apply_apt_mirror",
  "s3_restore",
  "install_game_mode",
  "extract_archive",
  "download_url",
  "cleanup_delete",
  "cleanup_system",
  "plugin_auto_update",
  "plugin_auto_update_test",
  "plugin_diagnostic_execute",
  "plugin_diagnostic_restore",
  "plugin_diagnostic_resume",
  "send_game_command",
  "test_initialized_ssh",
] as const;

export type ServerOperationAction = (typeof SERVER_OPERATION_ACTIONS)[number];

export function isServerOperationAction(value: string): value is ServerOperationAction {
  return (SERVER_OPERATION_ACTIONS as readonly string[]).includes(value);
}

export type ServerOperationStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed";

export type ServerOperation = {
  readonly operationId: string;
  readonly serverId: number;
  readonly action: ServerOperationAction;
  readonly status: ServerOperationStatus;
  readonly success: boolean | null;
  readonly message: string | null;
  readonly serverStatus: ServerStatus | null;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly actorUserId: number;
  readonly streamUrl: string;
  readonly command: string | null;
};

export type OperationInboxItem = ServerOperation & {
  readonly serverName: string;
  readonly latestMessage: string | null;
  readonly queuePosition: number;
};

export type OperationInbox = {
  readonly items: readonly OperationInboxItem[];
  readonly failedItems: readonly OperationInboxItem[];
  readonly activeCount: number;
  readonly runningCount: number;
  readonly failedCount: number;
  readonly failedRetentionDays: number;
};

export function isActiveOperation(operation: ServerOperation | null): boolean {
  return operation?.status === "queued" || operation?.status === "running";
}

export type OperationEventKind =
  | "status"
  | "output"
  | "error"
  | "complete"
  | "info";

export type OperationStreamEvent = {
  readonly sequence: string;
  readonly operationId: string;
  readonly type: string;
  readonly kind: string;
  readonly message: string;
  readonly timestamp: string;
  readonly success?: boolean;
  readonly serverStatus?: string | null;
};

export type OperationJournal = {
  readonly operation: ServerOperation;
  readonly events: readonly OperationStreamEvent[];
};

export type DeploymentLock = {
  readonly lockActive: boolean;
  readonly serverStatus: ServerStatus;
};

export type DeploymentLogEntry = {
  readonly id: number;
  readonly action: string;
  readonly status: string;
  readonly output: string | null;
  readonly errorMessage: string | null;
  readonly createdAt: string | null;
};

export const OPERATION_STATUS_TONE: Record<ServerOperationStatus, Tone> = {
  queued: "warn",
  running: "info",
  completed: "ok",
  failed: "danger",
};

const CONFIRM_ACTION_VALUES = [
  "deploy",
  "stop",
  "update",
  "validate",
  "backup_plugins",
  "install_metamod",
  "install_counterstrikesharp",
  "install_cs2fixes",
  "install_swiftly",
] as const satisfies readonly ServerOperationAction[];

export type ConfirmAction = (typeof CONFIRM_ACTION_VALUES)[number];

export const CONFIRM_ACTIONS: ReadonlySet<ConfirmAction> = new Set(
  CONFIRM_ACTION_VALUES,
);

export function requiresOperationConfirmation(
  action: ServerOperationAction,
): action is ConfirmAction {
  return (CONFIRM_ACTIONS as ReadonlySet<ServerOperationAction>).has(action);
}

export const CONFLICT_STRATEGIES = ["skip", "update", "rename"] as const;
export type ConflictStrategy = (typeof CONFLICT_STRATEGIES)[number];

export type ServerConfigImportAction =
  | "imported"
  | "updated"
  | "skipped"
  | "failed";

export type ServerConfigImportResult = {
  readonly index: number;
  readonly name: string;
  readonly action: ServerConfigImportAction;
  readonly serverId: number | null;
  readonly message: string | null;
};

export type ServerConfigImportSummary = {
  readonly total: number;
  readonly imported: number;
  readonly updated: number;
  readonly skipped: number;
  readonly failed: number;
  readonly results: readonly ServerConfigImportResult[];
};

/**
 * Wire-format portable bundle. Downloaded and re-uploaded as-is so backups
 * stay compatible with the legacy `/servers/export` document.
 */
export type ServerConfigBundle = {
  readonly format: "upkk-cs2-server-config";
  readonly version: number;
  readonly exported_at?: string | null;
  readonly include_secrets: boolean;
  readonly servers: readonly Record<string, unknown>[];
};

export type ServerConfigImportRequest = ServerConfigBundle & {
  readonly conflict_strategy: ConflictStrategy;
};

export type TransferServerOption = {
  readonly id: number;
  readonly name: string;
  readonly host: string;
  readonly gamePort: number;
};

export const IMPORT_ACTION_TONE: Record<ServerConfigImportAction, Tone> = {
  imported: "ok",
  updated: "info",
  skipped: "neutral",
  failed: "danger",
};
