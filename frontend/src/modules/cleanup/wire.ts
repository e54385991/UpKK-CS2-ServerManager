import type {
  CleanupItem,
  CleanupScan,
  CleanupSystemScan,
  CleanupSystemTarget,
} from "@/modules/cleanup/types";

export type CleanupItemViewDto = {
  path: string;
  name: string;
  type: string;
  size: number;
  category: string;
  reason: string;
  danger_level: string;
};

export type CleanupScanViewDto = {
  safe_items?: CleanupItemViewDto[];
  archive_items?: CleanupItemViewDto[];
  workshop_summary?: { path?: string; item_count?: number; size?: number };
  total_size?: number;
  safe_item_count?: number;
  archive_item_count?: number;
  truncated?: boolean;
};

export type CleanupSystemTargetDto = {
  id: string;
  title: string;
  reason: string;
  size: number;
  needs_privilege: boolean;
  can_apply: boolean;
  command: string | null;
};

export type CleanupSystemScanDto = {
  privilege: CleanupSystemScan["privilege"];
  retain_days: number;
  has_sudo_password: boolean;
  targets?: CleanupSystemTargetDto[];
  total_size?: number;
  can_apply_privileged?: boolean;
  manual_execute?: string[];
  manual_setup?: string[];
};

export function toCleanupItem(raw: CleanupItemViewDto): CleanupItem {
  return {
    path: raw.path,
    name: raw.name,
    type: raw.type,
    size: raw.size,
    category: raw.category,
    reason: raw.reason,
    dangerLevel: raw.danger_level,
  };
}

export function toCleanupScan(raw: CleanupScanViewDto): CleanupScan {
  const safeItems = (raw.safe_items ?? []).map(toCleanupItem);
  const archiveItems = (raw.archive_items ?? []).map(toCleanupItem);
  return {
    safeItems,
    archiveItems,
    workshopPath: raw.workshop_summary?.path ?? "",
    workshopCount: raw.workshop_summary?.item_count ?? 0,
    workshopSize: raw.workshop_summary?.size ?? 0,
    totalSize: raw.total_size ?? 0,
    safeItemCount: raw.safe_item_count ?? safeItems.length,
    archiveItemCount: raw.archive_item_count ?? archiveItems.length,
    truncated: Boolean(raw.truncated),
  };
}

export function toCleanupSystemTarget(raw: CleanupSystemTargetDto): CleanupSystemTarget {
  return {
    id: raw.id,
    title: raw.title,
    reason: raw.reason,
    size: raw.size,
    needsPrivilege: raw.needs_privilege,
    canApply: raw.can_apply,
    command: raw.command,
  };
}

export function toCleanupSystemScan(raw: CleanupSystemScanDto): CleanupSystemScan {
  return {
    privilege: raw.privilege,
    retainDays: raw.retain_days,
    hasSudoPassword: raw.has_sudo_password,
    targets: (raw.targets ?? []).map(toCleanupSystemTarget),
    totalSize: raw.total_size ?? 0,
    canApplyPrivileged: raw.can_apply_privileged ?? false,
    manualExecute: raw.manual_execute ?? [],
    manualSetup: raw.manual_setup ?? [],
  };
}
