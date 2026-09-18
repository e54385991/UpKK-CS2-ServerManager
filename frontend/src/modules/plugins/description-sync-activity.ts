import type { DescriptionSyncJob } from "@/modules/plugins/types";
import { openActivityTray } from "@/modules/servers/activity-store";

let submittedTask: DescriptionSyncJob | null = null;

export function latestSubmittedDescriptionSync(): DescriptionSyncJob | null {
  return submittedTask;
}

export function trackDescriptionSync(task: DescriptionSyncJob): void {
  submittedTask = task;
  window.dispatchEvent(
    new CustomEvent("plugin-description-sync-submitted", { detail: task }),
  );
  openActivityTray();
}
