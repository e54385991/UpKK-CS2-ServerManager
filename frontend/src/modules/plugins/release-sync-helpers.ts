import { deleteMarketPluginsAction } from "@/modules/plugins/actions";

export {
  ISSUE_TONE,
  RELEASE_TASK_ACTIVE,
  RELEASE_TASK_TERMINAL,
  isReleaseTaskActive,
  mergeReleaseTask,
  parseReleaseTask,
} from "@/modules/plugins/release-sync-state";

export async function deletePluginsInBatches(
  pluginIds: readonly number[],
): Promise<{ readonly ok: boolean; readonly deletedCount: number; readonly error?: string }> {
  const unique = [...new Set(pluginIds)];
  let deletedCount = 0;
  for (let offset = 0; offset < unique.length; offset += 500) {
    const chunk = unique.slice(offset, offset + 500);
    const result = await deleteMarketPluginsAction({ pluginIds: chunk });
    if (!result.ok) {
      return { ok: false, deletedCount, error: result.error };
    }
    deletedCount += chunk.length;
  }
  return { ok: true, deletedCount };
}
