import type { ReleaseSyncJob } from "@/modules/plugins/types";
import { openActivityTray } from "@/modules/servers/activity-store";

let submittedTask: ReleaseSyncJob | null = null;

export function latestSubmittedReleaseSync(): ReleaseSyncJob | null {
  return submittedTask;
}

export function trackReleaseSync(
  task: ReleaseSyncJob,
  options?: { readonly openTray?: boolean },
): void {
  submittedTask = task;
  window.dispatchEvent(
    new CustomEvent("plugin-release-sync-submitted", { detail: task }),
  );
  if (options?.openTray !== false) {
    openActivityTray();
  }
}
