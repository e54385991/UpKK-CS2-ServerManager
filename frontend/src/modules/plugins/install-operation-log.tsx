"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  mergeOperationEvents,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import {
  createRenderCoalescer,
  isTerminalOperationEventType,
} from "@/shared/lib/render-coalesce";
import type { OperationStreamEvent, ServerOperation } from "@/modules/servers/types";

export function InstallOperationLog({
  operation,
  pluginTitle,
  assetName,
  onOperation,
}: {
  operation: ServerOperation;
  pluginTitle: string;
  assetName?: string;
  onOperation: (update: (current: ServerOperation) => ServerOperation) => void;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);

  useEffect(() => {
    const source = new EventSource(
      operationEventsUrl(operation.serverId, operation.operationId),
    );
    const coalescer = createRenderCoalescer<OperationStreamEvent>((batch) => {
      setEvents((current) => mergeOperationEvents(current, batch));
    });
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return null;
      coalescer.push(event, {
        immediate: isTerminalOperationEventType(event.type),
      });
      return event;
    };
    source.onmessage = (message) => {
      ingest(message.data);
    };
    source.addEventListener("progress", (message: MessageEvent<string>) => {
      ingest(message.data);
    });
    source.addEventListener("operation_completed", (message: MessageEvent<string>) => {
      const event = ingest(message.data);
      onOperation((current) => ({
        ...current,
        status: "completed",
        success: true,
        message: event?.message ?? current.message,
      }));
      router.refresh();
    });
    source.addEventListener("operation_failed", (message: MessageEvent<string>) => {
      const event = ingest(message.data);
      onOperation((current) => ({
        ...current,
        status: "failed",
        success: false,
        message: event?.message ?? current.message,
      }));
    });
    return () => {
      coalescer.dispose();
      source.close();
    };
  }, [onOperation, operation, router]);

  return (
    <div
      className="rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
      data-testid="market-install-log"
    >
      <p className="text-sm font-medium text-fg">
        {t("liveLog")} · {operation.status}
      </p>
      <p className="mt-1 text-xs text-fg-subtle">
        {pluginTitle}
        {assetName ? ` · ${assetName}` : ""}
      </p>
      <pre className="mt-2 max-h-56 overflow-auto font-mono text-xs text-fg-muted">
        {events.length === 0 ? t("waitingLog") : events.map((event) => event.message).join("\n")}
      </pre>
    </div>
  );
}
