export const CLEANUP_MODES = ["safe", "archives", "workshop"] as const;

export type CleanupMode = (typeof CLEANUP_MODES)[number];

export type CleanupItem = {
  readonly path: string;
  readonly name: string;
  readonly type: string;
  readonly size: number;
  readonly category: string;
  readonly reason: string;
  readonly dangerLevel: string;
};

export type CleanupScan = {
  readonly safeItems: readonly CleanupItem[];
  readonly archiveItems: readonly CleanupItem[];
  readonly workshopPath: string;
  readonly workshopCount: number;
  readonly workshopSize: number;
  readonly totalSize: number;
  readonly safeItemCount: number;
  readonly archiveItemCount: number;
  readonly truncated: boolean;
};

export type CleanupDeleteResult = {
  readonly success: boolean;
  readonly message: string;
  readonly deletedCount: number;
  readonly freedBytes: number;
};

export const CLEANUP_SYSTEM_TARGETS = [
  "game_logs",
  "thumbnails",
  "apt_cache",
  "journal",
  "tmp",
  "crash",
  "rotated_logs",
] as const;

export type CleanupSystemTargetId = (typeof CLEANUP_SYSTEM_TARGETS)[number];
export type CleanupPrivilege = "root" | "sudo" | "none";

export type CleanupSystemTarget = {
  readonly id: string;
  readonly title: string;
  readonly reason: string;
  readonly size: number;
  readonly needsPrivilege: boolean;
  readonly canApply: boolean;
  readonly command: string | null;
};

export type CleanupSystemScan = {
  readonly privilege: CleanupPrivilege;
  readonly retainDays: number;
  readonly hasSudoPassword: boolean;
  readonly targets: readonly CleanupSystemTarget[];
  readonly totalSize: number;
  readonly canApplyPrivileged: boolean;
  readonly manualExecute: readonly string[];
  readonly manualSetup: readonly string[];
};

export type CleanupSystemApplyResult = {
  readonly success: boolean;
  readonly message: string;
  readonly privilege: CleanupPrivilege;
  readonly applied: readonly string[];
  readonly skipped: readonly { id: string; error: string }[];
  readonly failed: readonly { id: string; error: string }[];
  readonly deletedCount: number;
  readonly freedBytes: number;
  readonly manualExecute: readonly string[];
  readonly manualSetup: readonly string[];
};

export type CleanupPolicy = {
  readonly enabled: boolean;
  readonly retainDays: number;
  readonly scheduleValue: string;
  readonly targets: readonly string[];
  readonly hasSudoPassword: boolean;
  readonly lastRun: string | null;
  readonly nextRun: string | null;
  readonly lastStatus: string | null;
  readonly lastError: string | null;
  readonly runCount: number;
  readonly privilege: CleanupPrivilege | null;
  readonly manualExecute: readonly string[];
  readonly manualSetup: readonly string[];
  readonly message: string | null;
};
