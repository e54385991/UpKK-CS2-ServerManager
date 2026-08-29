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
};

export type CleanupDeleteResult = {
  readonly success: boolean;
  readonly message: string;
  readonly deletedCount: number;
  readonly freedBytes: number;
};
