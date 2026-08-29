"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { ListTodo, LoaderCircle, X } from "lucide-react";
import { isDeployProgressVisible } from "@/modules/console/live-console";
import { OpenLiveTerminalButton } from "@/modules/console/open-live-terminal";
import {
  closeActivityTray,
  dismissActivityOperations,
  markActivityTerminal,
  openActivityTray,
  selectActivityOperation,
  useActivityTray,
} from "@/modules/servers/activity-store";
import {
  clearFailedOperationsFromBrowser,
  dismissFailedOperationFromBrowser,
  loadOperationInboxFromBrowser,
  loadOperationJournalFromBrowser,
} from "@/modules/servers/operation-client";
import {
  OPERATION_STATUS_TONE,
  isActiveOperation,
  type OperationInbox,
  type OperationInboxItem,
  type OperationStreamEvent,
} from "@/modules/servers/types";
import {
  OPERATION_INBOX_EVENTS_URL,
  parseOperationInboxPayload,
} from "@/modules/servers/operation-inbox";
import {
  lastEventSequence,
  mergeOperationEvents,
  nextReconnectDelayMs,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import { confirm } from "@/shared/feedback";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

const GAME_ACTIONS = new Set(["start", "restart"]);

type TrayTab = "queue" | "failed";

function mergeById(
  groups: readonly (readonly OperationInboxItem[])[],
): OperationInboxItem[] {
  const byId = new Map<string, OperationInboxItem>();
  for (const group of groups) {
    for (const item of group) byId.set(item.operationId, item);
  }
  return [...byId.values()];
}

function ActivityConsole({ item }: { item: OperationInboxItem }) {
  const t = useTranslations("shell");
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);
  const itemRef = useRef(item);
  itemRef.current = item;

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let timer: number | null = null;
    let attempt = 0;
    const after = { current: "0" };
    void loadOperationJournalFromBrowser(item.serverId, item.operationId).then(
      (result) => {
        if (!cancelled && result.ok) {
          setEvents(mergeOperationEvents([], result.data.events));
          after.current = lastEventSequence(result.data.events);
        }
      },
    );
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return;
      if (event.sequence && event.sequence !== "seed") {
        after.current = event.sequence;
      }
      setEvents((current) => mergeOperationEvents(current, [event]));
      if (event.type === "operation_failed") {
        markActivityTerminal(item.operationId, "failed", event.message);
      } else if (event.type === "operation_completed") {
        markActivityTerminal(item.operationId, "completed", event.message);
      }
    };
    const attach = () => {
      if (cancelled) return;
      source = new EventSource(
        operationEventsUrl(item.serverId, item.operationId, after.current),
      );
      source.onopen = () => {
        attempt = 0;
      };
      source.onmessage = (message) => ingest(message.data);
      source.addEventListener("progress", (message: MessageEvent<string>) =>
        ingest(message.data),
      );
      source.addEventListener(
        "operation_completed",
        (message: MessageEvent<string>) => ingest(message.data),
      );
      source.addEventListener(
        "operation_failed",
        (message: MessageEvent<string>) => ingest(message.data),
      );
      source.onerror = () => {
        source?.close();
        source = null;
        if (cancelled) return;
        if (!isActiveOperation(itemRef.current)) return;
        const delay = nextReconnectDelayMs(attempt);
        attempt += 1;
        timer = window.setTimeout(attach, delay);
      };
    };
    attach();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
      source?.close();
    };
  }, [item.operationId, item.serverId]);

  const latest = item.latestMessage || events.at(-1)?.message || t("activityWaiting");

  return (
    <>
      <div>
        <p className="text-xs font-medium text-fg-subtle">{t("activityNow")}</p>
        <p className="mt-1 text-sm text-fg-muted" data-testid="activity-step">
          {latest}
        </p>
      </div>
      <div>
        <p className="text-xs font-medium text-fg-subtle">{t("activityLog")}</p>
        <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[11px] leading-5 text-fg-muted">
          {events.length === 0
            ? t("activityWaiting")
            : events.map((event) => event.message).join("\n")}
        </pre>
      </div>
    </>
  );
}

export function ActivityTray() {
  const t = useTranslations("shell");
  const tActions = useTranslations("serverDetail.actions");
  const tStatus = useTranslations("serverDetail");
  const { open, selectedId, overlay, dismissedIds } = useActivityTray();
  const [inbox, setInbox] = useState<OperationInbox | null>(null);
  const [tab, setTab] = useState<TrayTab>("queue");
  const dismissed = useMemo(() => new Set(dismissedIds), [dismissedIds]);
  const queue = useMemo(() => {
    return mergeById([overlay, inbox?.items ?? []])
      .filter((item) => !dismissed.has(item.operationId) && isActiveOperation(item))
      .sort((left, right) => {
        const rank = (status: string) => (status === "running" ? 0 : 1);
        const delta = rank(left.status) - rank(right.status);
        if (delta !== 0) return delta;
        return right.startedAt.localeCompare(left.startedAt);
      });
  }, [dismissed, inbox?.items, overlay]);
  const failed = useMemo(() => {
    return mergeById([overlay, inbox?.failedItems ?? []])
      .filter((item) => !dismissed.has(item.operationId) && item.status === "failed")
      .sort((left, right) =>
        (right.completedAt ?? right.startedAt).localeCompare(
          left.completedAt ?? left.startedAt,
        ),
      );
  }, [dismissed, inbox?.failedItems, overlay]);
  const remaining = queue.length;
  const failedCount = failed.length;
  const running = queue.some((item) => item.status === "running");
  const selectedIsFailed = Boolean(
    selectedId && failed.some((item) => item.operationId === selectedId),
  );
  const activeTab: TrayTab = tab === "queue" && selectedIsFailed ? "failed" : tab;
  const visible = activeTab === "queue" ? queue : failed;
  const selected =
    visible.find((item) => item.operationId === selectedId) ?? visible[0] ?? null;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const result = await loadOperationInboxFromBrowser();
      if (!cancelled && result.ok) setInbox(result.data);
    }
    void load();
    const source = new EventSource(OPERATION_INBOX_EVENTS_URL);
    const apply = (raw: string) => {
      const next = parseOperationInboxPayload(raw);
      if (!cancelled && next) setInbox(next);
    };
    source.addEventListener("inbox", (message: MessageEvent<string>) => apply(message.data));
    source.onmessage = (message) => apply(message.data);
    return () => {
      cancelled = true;
      source.close();
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadOperationInboxFromBrowser().then((result) => {
        if (result.ok) setInbox(result.data);
      });
    }, remaining > 0 ? 8000 : 20000);
    return () => window.clearInterval(timer);
  }, [remaining]);

  const actionLabel = (action: string) => {
    try {
      return tActions(action);
    } catch {
      return action;
    }
  };

  async function clearFailed() {
    if (failedCount === 0) return;
    if (
      !(await confirm({
        title: t("activityClearFailed"),
        description: t("activityClearFailedConfirm"),
        tone: "danger",
      }))
    ) {
      return;
    }
    const result = await clearFailedOperationsFromBrowser();
    if (!result.ok) return;
    dismissActivityOperations(failed.map((item) => item.operationId));
    const inboxResult = await loadOperationInboxFromBrowser();
    if (inboxResult.ok) setInbox(inboxResult.data);
  }

  async function dismissOne(operationId: string) {
    const result = await dismissFailedOperationFromBrowser(operationId);
    if (!result.ok) return;
    dismissActivityOperations([operationId]);
    const inboxResult = await loadOperationInboxFromBrowser();
    if (inboxResult.ok) setInbox(inboxResult.data);
  }

  return (
    <div className="relative">
      <Button
        type="button"
        variant={remaining > 0 ? "primary" : "outline"}
        size="sm"
        data-testid="activity-tray-toggle"
        data-busy={remaining > 0 ? "true" : "false"}
        aria-expanded={open}
        aria-label={
          remaining > 0
            ? t("activityOpenBusy", { count: remaining })
            : t("activityOpen")
        }
        className={cn(
          "relative gap-2 overflow-visible",
          remaining > 0 &&
            "shadow-[0_0_0_1px_rgb(34_211_238/0.55),0_0_22px_rgb(34_211_238/0.28)]",
        )}
        onClick={() => {
          if (open) {
            closeActivityTray();
            return;
          }
          setTab(remaining === 0 && failedCount > 0 ? "failed" : "queue");
          openActivityTray(selected?.operationId);
        }}
      >
        {remaining > 0 ? (
          <span
            aria-hidden
            className="pointer-events-none absolute -inset-1.5 -z-10 animate-ping rounded-lg bg-primary/35"
          />
        ) : null}
        <span className="relative inline-flex">
          {running ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <ListTodo className={cn("size-4", remaining > 0 && "animate-pulse")} />
          )}
        </span>
        <span>
          {remaining > 0
            ? t("activityRemaining", { count: remaining })
            : t("activityTitle")}
        </span>
        {remaining > 0 ? (
          <span
            data-testid="activity-tray-count"
            className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary-foreground px-1.5 text-[11px] font-semibold text-primary-strong"
          >
            {remaining}
          </span>
        ) : null}
        {failedCount > 0 ? (
          <span
            data-testid="activity-tray-failed-count"
            className="inline-flex min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-[11px] font-semibold text-white"
          >
            {failedCount}
          </span>
        ) : null}
        <StatusDot
          tone={running ? "primary" : remaining > 0 ? "warn" : failedCount > 0 ? "danger" : "neutral"}
          pulse={remaining > 0}
        />
      </Button>

      {open ? (
        <div
          role="dialog"
          aria-label={t("activityTitle")}
          data-testid="activity-tray-panel"
          className="absolute right-0 z-40 mt-2 flex w-[min(28rem,calc(100vw-2rem))] max-h-[min(36rem,70dvh)] flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-panel"
        >
          <header className="space-y-3 border-b border-line px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-fg">{t("activityTitle")}</p>
                <p className="text-xs text-fg-subtle">
                  {activeTab === "queue"
                    ? remaining > 0
                      ? t("activityRemaining", { count: remaining })
                      : t("activityEmpty")
                    : failedCount > 0
                      ? t("activityFailedCount", { count: failedCount })
                      : t("activityFailedEmpty")}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t("activityClose")}
                onClick={() => closeActivityTray()}
              >
                <X />
              </Button>
            </div>
            <div
              role="tablist"
              aria-label={t("activityTitle")}
              className="flex rounded-md border border-line bg-surface-raised p-0.5"
            >
              {(
                [
                  ["queue", t("activityTabQueue"), remaining],
                  ["failed", t("activityTabFailed"), failedCount],
                ] as const
              ).map(([id, label, count]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  data-testid={`activity-tray-tab-${id}`}
                  aria-selected={activeTab === id}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-1.5 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-colors",
                    activeTab === id
                      ? "bg-surface text-fg shadow-sm"
                      : "text-fg-muted hover:text-fg",
                  )}
                  onClick={() => {
                    if (id === "queue") {
                      selectActivityOperation(queue[0]?.operationId ?? null);
                    }
                    setTab(id);
                  }}
                >
                  {label}
                  {count > 0 ? (
                    <span
                      className={cn(
                        "inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold",
                        id === "failed"
                          ? "bg-danger text-white"
                          : "bg-primary text-primary-foreground",
                      )}
                    >
                      {count}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {visible.length === 0 ? (
              <p className="px-4 py-6 text-sm text-fg-muted">
                {activeTab === "queue" ? t("activityEmptyHelp") : t("activityFailedHelp")}
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {visible.map((item) => (
                  <li key={item.operationId} className="flex items-stretch">
                    <button
                      type="button"
                      className={cn(
                        "flex min-w-0 flex-1 flex-col gap-1 px-4 py-3 text-left hover:bg-surface-overlay",
                        selected?.operationId === item.operationId && "bg-surface-overlay",
                      )}
                      onClick={() => selectActivityOperation(item.operationId)}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-fg">
                          {actionLabel(item.action)}
                        </span>
                        <Badge tone={OPERATION_STATUS_TONE[item.status]}>
                          {tStatus(`opStatus.${item.status}`)}
                        </Badge>
                      </span>
                      <span className="truncate font-mono text-[11px] text-fg-muted">
                        {item.command || actionLabel(item.action)}
                      </span>
                      <span className="truncate text-xs text-fg-subtle">
                        {item.serverName}
                        {item.queuePosition > 0
                          ? ` · ${t("activityPosition", { position: item.queuePosition })}`
                          : ""}
                      </span>
                    </button>
                    {activeTab === "failed" ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="m-1 shrink-0"
                        aria-label={t("activityDismissFailed")}
                        onClick={() => void dismissOne(item.operationId)}
                      >
                        <X />
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {activeTab === "failed" && failedCount > 0 ? (
              <div className="border-t border-line px-4 py-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid="activity-tray-clear-failed"
                  onClick={() => void clearFailed()}
                >
                  {t("activityClearFailed")}
                </Button>
              </div>
            ) : null}
            {selected ? (
              <div className="space-y-3 border-t border-line px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium text-fg">
                    {actionLabel(selected.action)}
                  </p>
                  <Badge
                    data-testid="activity-status"
                    tone={OPERATION_STATUS_TONE[selected.status]}
                  >
                    {tStatus(`opStatus.${selected.status}`)}
                  </Badge>
                </div>
                <div>
                  <p className="text-xs font-medium text-fg-subtle">{t("activityCommand")}</p>
                  <pre
                    data-testid="activity-command"
                    className="mt-1 overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-fg"
                  >
                    {selected.command || actionLabel(selected.action)}
                  </pre>
                </div>
                <ActivityConsole key={selected.operationId} item={selected} />
                <div className="flex flex-wrap gap-2">
                  <Link
                    href={`/servers/${selected.serverId}/operations` as Route}
                    className="text-xs text-primary hover:underline"
                  >
                    {t("activityOpenOperations")}
                  </Link>
                  {isDeployProgressVisible({ operation: selected }) ? (
                    <OpenLiveTerminalButton
                      serverId={selected.serverId}
                      view="deploy"
                      label={t("activityOpenTmux")}
                    />
                  ) : null}
                  {GAME_ACTIONS.has(selected.action) ? (
                    <OpenLiveTerminalButton
                      serverId={selected.serverId}
                      view="game"
                      label={t("activityOpenTmux")}
                    />
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
