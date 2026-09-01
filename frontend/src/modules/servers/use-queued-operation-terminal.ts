"use client";

import { useEffect, useRef } from "react";
import { useActivityTray } from "@/modules/servers/activity-store";
import {
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";

type TerminalStatus = "completed" | "failed";

/**
 * Follow a hub job until it finishes. Uses the activity-tray overlay when the
 * tray already marked the operation terminal, and the operation SSE stream so
 * the page still updates if the tray panel is closed.
 */
export function useQueuedOperationTerminal(
  operationId: string | null,
  serverId: number | null,
  onTerminal: (status: TerminalStatus, message: string | null) => void,
) {
  const onTerminalRef = useRef(onTerminal);
  const seenRef = useRef<string | null>(null);
  const { overlay } = useActivityTray();

  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

  useEffect(() => {
    seenRef.current = null;
  }, [operationId]);

  useEffect(() => {
    if (!operationId) return;
    const item = overlay.find((entry) => entry.operationId === operationId);
    if (item?.status !== "completed" && item?.status !== "failed") return;
    if (seenRef.current === operationId) return;
    seenRef.current = operationId;
    onTerminalRef.current(item.status, item.latestMessage);
  }, [operationId, overlay]);

  useEffect(() => {
    if (!operationId || serverId == null) return;
    const after = { current: "0" };
    return subscribeVisibleEventSource({
      url: () => operationEventsUrl(serverId, operationId, after.current),
      eventTypes: ["progress", "operation_completed", "operation_failed"],
      onData: (raw) => {
        const event = parseOperationEvent(raw);
        if (!event) return;
        if (event.sequence && event.sequence !== "seed") {
          after.current = event.sequence;
        }
        if (event.type !== "operation_failed" && event.type !== "operation_completed") {
          return;
        }
        if (seenRef.current === operationId) return;
        seenRef.current = operationId;
        onTerminalRef.current(
          event.type === "operation_failed" ? "failed" : "completed",
          event.message,
        );
      },
    });
  }, [operationId, serverId]);
}
