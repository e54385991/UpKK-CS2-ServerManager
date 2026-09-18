"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import type { Route } from "next";
import { memo, useCallback } from "react";
import { useTranslations } from "next-intl";
import { Ban, LoaderCircle, Trash2, X } from "lucide-react";
import { isDeployProgressVisible } from "@/modules/console/live-console";
import { OpenLiveTerminalButton } from "@/modules/console/open-live-terminal";
import { closeActivityTray, selectActivityOperation } from "@/modules/servers/activity-store";
import {
  isServerOperationAction,
  OPERATION_STATUS_TONE,
  type OperationInbox,
  type OperationInboxItem,
} from "@/modules/servers/types";
import { ActivityConsole } from "@/modules/shell/activity-console";
import type { TrayTab } from "@/modules/shell/activity-tray-lists";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { DialogContentLoading } from "@/shared/ui/dialog-loading";
import { cn } from "@/shared/lib/cn";

const AIImportTasks = dynamic(
  () => import("@/modules/plugins/ai-import-tasks").then((mod) => mod.AIImportTasks),
  { loading: DialogContentLoading },
);

const GAME_ACTIONS = new Set(["start", "restart"]);

type ActivityTrayPanelProps = {
  readonly isAdmin: boolean;
  readonly hasVisibleMarketTasks: boolean;
  readonly marketTasks: readonly (
    | NonNullable<OperationInbox["marketImportItems"]>[number]
    | NonNullable<OperationInbox["marketDescriptionItems"]>[number]
  )[];
  readonly activeTab: TrayTab;
  readonly remaining: number;
  readonly completedCount: number;
  readonly failedCount: number;
  readonly allFailedCount: number;
  readonly queue: readonly OperationInboxItem[];
  readonly completed: readonly OperationInboxItem[];
  readonly failed: readonly OperationInboxItem[];
  readonly visible: readonly OperationInboxItem[];
  readonly selected: OperationInboxItem | null;
  readonly cancellingId: string | null;
  readonly onTab: (tab: TrayTab) => void;
  readonly onClearCompleted: () => void;
  readonly onClearFailed: () => void;
  readonly onForceStop: (item: OperationInboxItem) => void | Promise<void>;
  readonly onDismiss: (operationId: string, tab: "completed" | "failed") => void | Promise<void>;
};

const ActivityTrayRow = memo(function ActivityTrayRow({
  item,
  selected,
  activeTab,
  cancellingId,
  actionLabel,
  onSelect,
  onForceStop,
  onDismiss,
}: {
  item: OperationInboxItem;
  selected: boolean;
  activeTab: TrayTab;
  cancellingId: string | null;
  actionLabel: (action: string) => string;
  onSelect: (operationId: string) => void;
  onForceStop: (item: OperationInboxItem) => void;
  onDismiss: (operationId: string, tab: "completed" | "failed") => void;
}) {
  const t = useTranslations("shell");
  const tStatus = useTranslations("serverDetail");
  return (
    <li className="flex items-stretch">
      <button
        type="button"
        className={cn(
          "flex min-w-0 flex-1 flex-col gap-1 px-4 py-3 text-left hover:bg-surface-overlay",
          selected && "bg-surface-overlay",
        )}
        onClick={() => onSelect(item.operationId)}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-fg">{actionLabel(item.action)}</span>
          <Badge tone={OPERATION_STATUS_TONE[item.status]}>{tStatus(`opStatus.${item.status}`)}</Badge>
        </span>
        <span className="truncate font-mono text-[11px] text-fg-muted">
          {item.command || actionLabel(item.action)}
        </span>
        <span className="truncate text-xs text-fg-subtle">
          {item.serverName}
          {item.queuePosition > 0 ? ` · ${t("activityPosition", { position: item.queuePosition })}` : ""}
        </span>
      </button>
      {activeTab === "queue" ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="m-1 shrink-0 text-danger hover:bg-danger/10"
          aria-label={t("activityForceStop")}
          disabled={cancellingId === item.operationId}
          onClick={(event) => {
            event.stopPropagation();
            onForceStop(item);
          }}
        >
          {cancellingId === item.operationId ? <LoaderCircle className="animate-spin" /> : <Ban />}
        </Button>
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="m-1 shrink-0"
          aria-label={
            activeTab === "completed" ? t("activityDismissCompleted") : t("activityDismissFailed")
          }
          onClick={() => onDismiss(item.operationId, activeTab)}
        >
          <X />
        </Button>
      )}
    </li>
  );
});

export function ActivityTrayPanel({
  isAdmin,
  hasVisibleMarketTasks,
  marketTasks,
  activeTab,
  remaining,
  completedCount,
  failedCount,
  allFailedCount,
  queue,
  completed,
  failed,
  visible,
  selected,
  cancellingId,
  onTab,
  onClearCompleted,
  onClearFailed,
  onForceStop,
  onDismiss,
}: ActivityTrayPanelProps) {
  const t = useTranslations("shell");
  const tActions = useTranslations("serverDetail.actions");
  const tStatus = useTranslations("serverDetail");
  const actionLabel = useCallback(
    (action: string) => (isServerOperationAction(action) ? tActions(action) : action),
    [tActions],
  );

  return (
    <div
      role="dialog"
      aria-label={t("activityTitle")}
      data-testid="activity-tray-panel"
      className="fixed right-4 sm:absolute sm:right-0 z-40 mt-2 flex w-[min(28rem,calc(100vw-2rem))] max-h-[min(36rem,70dvh)] flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-panel"
    >
      {isAdmin && hasVisibleMarketTasks && <AIImportTasks initialTasks={marketTasks} />}
      <header className="space-y-3 border-b border-line px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-fg">{t("activityTitle")}</p>
            <p className="text-xs text-fg-subtle">
              {activeTab === "queue"
                ? remaining > 0
                  ? t("activityRemaining", { count: remaining })
                  : t("activityEmpty")
                : activeTab === "completed"
                  ? completedCount > 0
                    ? t("activityCompletedCount", { count: completedCount })
                    : t("activityCompletedEmpty")
                  : failedCount > 0
                    ? t("activityFailedCount", { count: failedCount })
                    : t("activityFailedEmpty")}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {activeTab === "completed" && completedCount > 0 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-danger hover:bg-danger/10"
                data-testid="activity-tray-clear-completed"
                onClick={onClearCompleted}
              >
                <Trash2 className="size-4" />
                {t("activityClearCompletedAll", { count: completedCount })}
              </Button>
            ) : null}
            {allFailedCount > 0 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-danger hover:bg-danger/10"
                data-testid="activity-tray-clear-failed"
                onClick={onClearFailed}
              >
                <Trash2 className="size-4" />
                {t("activityClearFailedAll", { count: allFailedCount })}
              </Button>
            ) : null}
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
        </div>
        <div role="tablist" aria-label={t("activityTitle")} className="flex rounded-md border border-line bg-surface-raised p-0.5">
          {(
            [
              ["queue", t("activityTabQueue"), remaining],
              ["completed", t("activityTabCompleted"), completedCount],
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
                activeTab === id ? "bg-surface text-fg shadow-sm" : "text-fg-muted hover:text-fg",
              )}
              onClick={() => {
                if (id === "queue") {
                  selectActivityOperation(queue[0]?.operationId ?? null);
                }
                if (id === "completed") {
                  selectActivityOperation(completed[0]?.operationId ?? null);
                }
                if (id === "failed") {
                  selectActivityOperation(failed[0]?.operationId ?? null);
                }
                onTab(id);
              }}
            >
              {label}
              {count > 0 ? (
                <span
                  className={cn(
                    "inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold",
                    id === "failed" ? "bg-danger text-white" : "bg-primary text-primary-foreground",
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
            {activeTab === "queue"
              ? t("activityEmptyHelp")
              : activeTab === "completed"
                ? t("activityCompletedHelp")
                : t("activityFailedHelp")}
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {visible.map((item) => (
              <ActivityTrayRow
                key={item.operationId}
                item={item}
                selected={selected?.operationId === item.operationId}
                activeTab={activeTab}
                cancellingId={cancellingId}
                actionLabel={actionLabel}
                onSelect={selectActivityOperation}
                onForceStop={onForceStop}
                onDismiss={onDismiss}
              />
            ))}
          </ul>
        )}
        {selected ? (
          <div className="space-y-3 border-t border-line px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-sm font-medium text-fg">{actionLabel(selected.action)}</p>
              <Badge data-testid="activity-status" tone={OPERATION_STATUS_TONE[selected.status]}>
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
              {selected.serverId > 0 ? (
                <Link
                  href={`/servers/${selected.serverId}/operations` as Route}
                  className="text-xs text-primary hover:underline"
                >
                  {t("activityOpenOperations")}
                </Link>
              ) : null}
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
  );
}
