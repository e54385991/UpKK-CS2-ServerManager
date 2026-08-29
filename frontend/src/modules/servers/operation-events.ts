import type { OperationStreamEvent } from "@/modules/servers/types";

export const OPERATION_EVENT_LIMIT = 300;

export function operationEventsUrl(
  serverId: number,
  operationId: string,
  after = "0",
): string {
  return `/ops-stream/servers/${serverId}/operations/${operationId}?after=${encodeURIComponent(after)}`;
}

export function lastEventSequence(
  events: readonly { sequence: string }[],
): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const sequence = events[index]?.sequence ?? "";
    if (sequence && sequence !== "seed") return sequence;
  }
  return "0";
}

export function nextReconnectDelayMs(attempt: number): number {
  return Math.min(8000, 400 * 2 ** Math.min(Math.max(attempt, 0), 4));
}

export function parseOperationEvent(raw: string): OperationStreamEvent | null {
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    if (typeof data.message !== "string") {
      return null;
    }
    return {
      sequence: String(data.sequence ?? ""),
      operationId: String(data.operation_id ?? ""),
      type: typeof data.type === "string" ? data.type : "progress",
      kind: String(data.kind ?? "output"),
      message: data.message,
      timestamp: String(data.timestamp ?? ""),
      success: typeof data.success === "boolean" ? data.success : undefined,
      serverStatus:
        typeof data.server_status === "string" ? data.server_status : null,
    };
  } catch {
    return null;
  }
}

function sequenceSortKey(value: string): string {
  if (!value || value === "seed") return "0".padStart(24, "0");
  return value.padStart(24, "0");
}

export function mergeOperationEvents(
  current: readonly OperationStreamEvent[],
  incoming: readonly OperationStreamEvent[],
): OperationStreamEvent[] {
  if (incoming.length === 0) {
    return current.length > OPERATION_EVENT_LIMIT
      ? current.slice(-OPERATION_EVENT_LIMIT)
      : [...current];
  }
  const seen = new Set(
    current.map((event) => event.sequence).filter((value) => value.length > 0),
  );
  const next = [...current];
  for (const event of incoming) {
    if (event.sequence && seen.has(event.sequence)) continue;
    if (event.sequence) seen.add(event.sequence);
    next.push(event);
  }
  next.sort((left, right) =>
    sequenceSortKey(left.sequence).localeCompare(sequenceSortKey(right.sequence)),
  );
  return next.length > OPERATION_EVENT_LIMIT
    ? next.slice(-OPERATION_EVENT_LIMIT)
    : next;
}
