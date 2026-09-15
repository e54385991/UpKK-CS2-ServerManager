import type { AssistantTool } from "./types";

export function mergeTools(
  current: readonly AssistantTool[],
  incoming: readonly AssistantTool[],
): AssistantTool[] {
  const next = [...current];
  for (const tool of incoming) {
    const existing = next.find((item) => item.id === tool.id);
    if (!existing) {
      next.push(tool);
    } else if (
      Object.keys(existing.arguments).length === 0 &&
      Object.keys(tool.arguments).length > 0
    ) {
      next[next.indexOf(existing)] = tool;
    }
  }
  return next;
}
