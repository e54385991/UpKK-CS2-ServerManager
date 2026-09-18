"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { ListTodo, LoaderCircle } from "lucide-react";
import {
  closeActivityTray,
  openActivityTray,
  useActivityTray,
} from "@/modules/servers/activity-store";
import { ActivityTrayPanel } from "@/modules/shell/activity-tray-panel";
import {
  countActiveMarketTasks,
  countFailedMarketTasks,
  deriveCachedActivityLists,
  type TrayTab,
} from "@/modules/shell/activity-tray-lists";
import { useActivityCommands } from "@/modules/shell/use-activity-commands";
import { setInboxPollRemaining, useActivityInbox } from "@/modules/shell/use-activity-inbox";
import { StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

export function ActivityTray({ isAdmin = false }: { isAdmin?: boolean }) {
  const t = useTranslations("shell");
  const { open, selectedId, overlay, dismissedIds } = useActivityTray();
  const { inbox, setInbox } = useActivityInbox();
  const [tab, setTab] = useState<TrayTab>("queue");
  const dismissed = useMemo(() => new Set(dismissedIds), [dismissedIds]);
  const lists = useMemo(
    () => deriveCachedActivityLists(inbox, overlay, dismissed),
    [dismissed, inbox, overlay],
  );
  const { queue, completed, failed } = lists;
  const marketTasks = [
    ...(inbox?.marketImportItems ?? []),
    ...(inbox?.marketDescriptionItems ?? []),
  ];
  const hasVisibleMarketTasks = marketTasks.length > 0;
  const remaining = queue.length + countActiveMarketTasks(marketTasks);
  const completedCount = completed.length;
  const failedCount = failed.length;
  const allFailedCount = failedCount + countFailedMarketTasks(marketTasks);
  const running =
    queue.some((item) => item.status === "running") ||
    marketTasks.some((item) => item.status === "running");
  const selectedIsFailed = Boolean(
    selectedId && failed.some((item) => item.operationId === selectedId),
  );
  const activeTab: TrayTab = tab === "queue" && selectedIsFailed ? "failed" : tab;
  const visible = activeTab === "queue" ? queue : activeTab === "completed" ? completed : failed;
  const selected =
    visible.find((item) => item.operationId === selectedId) ?? visible[0] ?? null;
  const commands = useActivityCommands({
    completed,
    failed,
    completedCount,
    allFailedCount,
    setInbox,
  });

  useEffect(() => {
    setInboxPollRemaining(remaining);
  }, [remaining]);

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
          remaining > 0 ? t("activityOpenBusy", { count: remaining }) : t("activityOpen")
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
          setTab(remaining > 0 ? "queue" : failedCount > 0 ? "failed" : "completed");
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
        <span>{remaining > 0 ? t("activityRemaining", { count: remaining }) : t("activityTitle")}</span>
        {remaining > 0 ? (
          <span
            data-testid="activity-tray-count"
            className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary-foreground px-1.5 text-[11px] font-semibold text-primary-strong"
          >
            {remaining}
          </span>
        ) : null}
        {allFailedCount > 0 ? (
          <span
            data-testid="activity-tray-failed-count"
            className="inline-flex min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-[11px] font-semibold text-white"
          >
            {allFailedCount}
          </span>
        ) : null}
        <StatusDot
          tone={running ? "primary" : remaining > 0 ? "warn" : allFailedCount > 0 ? "danger" : "neutral"}
          pulse={remaining > 0}
        />
      </Button>

      {open ? (
        <ActivityTrayPanel
          isAdmin={isAdmin}
          hasVisibleMarketTasks={hasVisibleMarketTasks}
          marketTasks={marketTasks}
          activeTab={activeTab}
          remaining={remaining}
          completedCount={completedCount}
          failedCount={failedCount}
          allFailedCount={allFailedCount}
          queue={queue}
          completed={completed}
          failed={failed}
          visible={visible}
          selected={selected}
          cancellingId={commands.cancellingId}
          onTab={setTab}
          onClearCompleted={() => void commands.clearCompleted()}
          onClearFailed={() => void commands.clearFailed()}
          onForceStop={commands.forceStopOne}
          onDismiss={commands.dismissTerminalOne}
        />
      ) : null}
    </div>
  );
}
