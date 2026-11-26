import type { ReleaseIssueCode, ReleaseSyncJob } from "./types.ts";

export const RELEASE_TASK_ACTIVE = new Set(["queued", "running", "waiting"]);
export const RELEASE_TASK_TERMINAL = new Set(["completed", "failed", "cancelled"]);

export const ISSUE_TONE: Record<
  ReleaseIssueCode,
  "danger" | "warn" | "info" | "neutral"
> = {
  invalid_url: "danger",
  repository_missing: "danger",
  release_missing: "danger",
  no_linux_asset: "danger",
  archive_invalid: "danger",
  manual_selection_required: "warn",
  upstream_error: "neutral",
};

export function isReleaseTaskActive(task: Pick<ReleaseSyncJob, "status">): boolean {
  return RELEASE_TASK_ACTIVE.has(task.status);
}

function statusRank(task: Pick<ReleaseSyncJob, "status">): number {
  return RELEASE_TASK_TERMINAL.has(task.status) ? 3 : 1;
}

export function mergeReleaseTask(
  current: ReleaseSyncJob | undefined,
  candidate: ReleaseSyncJob,
): ReleaseSyncJob {
  if (!current) return candidate;
  if (statusRank(current) === 3 && statusRank(candidate) < 3) return current;
  if (statusRank(candidate) === 3) return candidate;
  if (candidate.processed >= current.processed) return candidate;
  return current;
}

export function parseReleaseTask(raw: string): ReleaseSyncJob | null {
  try {
    const value = JSON.parse(raw) as Partial<ReleaseSyncJob>;
    if (
      typeof value.operation_id !== "string" ||
      typeof value.status !== "string" ||
      typeof value.processed !== "number" ||
      typeof value.total !== "number" ||
      typeof value.cleanable !== "number"
    ) {
      return null;
    }
    return value as ReleaseSyncJob;
  } catch {
    return null;
  }
}
