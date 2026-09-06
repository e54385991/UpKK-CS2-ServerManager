import type { components } from "@/shared/api/schema";
import { openActivityTray } from "@/modules/servers/activity-store";

type Task = components["schemas"]["PluginAIImportView"];
let submittedTask: Task | null = null;

export function latestSubmittedAIImport(): Task | null {
  return submittedTask;
}

export function trackAIImport(task: Task): void {
  submittedTask = task;
  window.dispatchEvent(new CustomEvent("plugin-ai-import-submitted", { detail: task }));
  openActivityTray();
}
