"use client";

import { useEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { markActivityTerminal } from "@/modules/servers/activity-store";
import { initializedHostOperationEventsUrl } from "@/modules/servers/initialized-host-operation-events";
import {
  lastEventSequence,
  mergeOperationEvents,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import { loadOperationJournalFromBrowser } from "@/modules/servers/operation-client";
import {
  isActiveOperation,
  type OperationInboxItem,
  type OperationStreamEvent,
} from "@/modules/servers/types";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";
import {
  createRenderCoalescer,
  isTerminalOperationEventType,
} from "@/shared/lib/render-coalesce";
import { cn } from "@/shared/lib/cn";

export function ActivityConsole({ item }: { item: OperationInboxItem }) {
  const t = useTranslations("shell");
  const tDetail = useTranslations("serverDetail");
  const format = useFormatter();
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);
  const itemRef = useRef(item);

  useEffect(() => {
    itemRef.current = item;
  }, [item]);

  useEffect(() => {
    let cancelled = false;
    const after = { current: "0" };
    const journalAbort = new AbortController();
    const coalescer = createRenderCoalescer<OperationStreamEvent>((batch) => {
      setEvents((current) => mergeOperationEvents(current, batch));
    });
    if (item.serverId > 0) {
      void loadOperationJournalFromBrowser(item.serverId, item.operationId, {
        signal: journalAbort.signal,
      }).then((result) => {
        if (!cancelled && result.ok) {
          setEvents((current) => mergeOperationEvents(current, result.data.events));
          if (after.current === "0") {
            after.current = lastEventSequence(result.data.events);
          }
        }
      });
    }
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return;
      if (event.sequence && event.sequence !== "seed") {
        after.current = event.sequence;
      }
      coalescer.push(event, {
        immediate: isTerminalOperationEventType(event.type),
      });
      if (event.type === "operation_failed") {
        markActivityTerminal(item.operationId, "failed", event.message);
      } else if (event.type === "operation_completed") {
        markActivityTerminal(item.operationId, "completed", event.message);
      }
    };
    const stop = subscribeVisibleEventSource({
      url: () =>
        item.serverId < 0
          ? initializedHostOperationEventsUrl(-item.serverId, item.operationId, after.current)
          : operationEventsUrl(item.serverId, item.operationId, after.current),
      eventTypes: ["progress", "operation_completed", "operation_failed"],
      shouldReconnect: () => isActiveOperation(itemRef.current),
      onData: ingest,
    });
    return () => {
      cancelled = true;
      coalescer.dispose();
      journalAbort.abort();
      stop();
    };
  }, [item.operationId, item.serverId]);

  const latest = item.latestMessage || events.at(-1)?.message || t("activityWaiting");
  const transfer = [...events].reverse().find((event) => event.transfer)?.transfer ?? null;
  const transferPhase = transfer
    ? transfer.phase === "download"
      ? tDetail("transferDownload")
      : tDetail("transferUpload")
    : "";
  const transferred = transfer
    ? `${format.number(transfer.bytesTransferred / (1024 * 1024), { maximumFractionDigits: 1 })} MB`
    : "";
  const progressLabel =
    transfer && transfer.percent !== null
      ? t("activityProgress", {
          phase: transferPhase,
          percent: `${format.number(transfer.percent, { maximumFractionDigits: 1 })}%`,
        })
      : transfer
        ? t("activityProgressBytes", { phase: transferPhase, transferred })
        : null;
  const retryLabel =
    transfer && transfer.retryCount > 0 ? t("activityRetries", { count: transfer.retryCount }) : null;

  return (
    <>
      <div>
        <p className="text-xs font-medium text-fg-subtle">{t("activityNow")}</p>
        <p className="mt-1 text-sm text-fg-muted" data-testid="activity-step">
          {latest}
        </p>
      </div>
      {transfer && progressLabel ? (
        <div className="space-y-1" data-testid="activity-transfer-progress">
          <div className="flex items-center justify-between gap-2 text-xs text-fg-subtle">
            <span>
              {progressLabel}
              {retryLabel ? ` · ${retryLabel}` : ""}
            </span>
            <span>{tDetail("transferElapsed", { seconds: transfer.elapsedSeconds.toFixed(1) })}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className={cn(
                "h-full rounded-full bg-primary transition-[width] duration-500",
                transfer.percent === null ? "w-1/3 animate-pulse" : "",
              )}
              style={
                transfer.percent === null ? undefined : { width: `${transfer.percent}%` }
              }
              aria-label={progressLabel}
              aria-valuenow={transfer.percent ?? undefined}
              aria-valuemin={0}
              aria-valuemax={100}
              role="progressbar"
            />
          </div>
        </div>
      ) : null}
      <div>
        <p className="text-xs font-medium text-fg-subtle">{t("activityLog")}</p>
        <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[11px] leading-5 text-fg-muted">
          {events.length === 0 ? t("activityWaiting") : events.map((event) => event.message).join("\n")}
        </pre>
      </div>
    </>
  );
}
