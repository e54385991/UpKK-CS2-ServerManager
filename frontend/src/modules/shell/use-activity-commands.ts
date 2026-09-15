"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  dismissActivityOperations,
  markActivityTerminal,
} from "@/modules/servers/activity-store";
import {
  cancelOperationFromBrowser,
  clearCompletedOperationsFromBrowser,
  clearFailedOperationsFromBrowser,
  dismissCompletedOperationFromBrowser,
  dismissFailedOperationFromBrowser,
  loadOperationInboxFromBrowser,
} from "@/modules/servers/operation-client";
import type { OperationInbox, OperationInboxItem } from "@/modules/servers/types";
import { confirm, notify } from "@/shared/feedback";

export function useActivityCommands(options: {
  readonly completed: readonly OperationInboxItem[];
  readonly failed: readonly OperationInboxItem[];
  readonly completedCount: number;
  readonly allFailedCount: number;
  readonly setInbox: (inbox: OperationInbox) => void;
}) {
  const t = useTranslations("shell");
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const refreshInbox = useCallback(async () => {
    const inboxResult = await loadOperationInboxFromBrowser();
    if (inboxResult.ok) optionsRef.current.setInbox(inboxResult.data);
  }, []);

  const clearFailed = useCallback(async () => {
    const current = optionsRef.current;
    if (current.allFailedCount === 0) return;
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
    if (!result.ok) {
      notify.error(result.error || t("activityClearFailedError"));
      return;
    }
    dismissActivityOperations(current.failed.map((item) => item.operationId));
    window.dispatchEvent(new Event("plugin-ai-import-refresh"));
    await refreshInbox();
  }, [refreshInbox, t]);

  const clearCompleted = useCallback(async () => {
    const current = optionsRef.current;
    if (current.completedCount === 0) return;
    if (
      !(await confirm({
        title: t("activityClearCompleted"),
        description: t("activityClearCompletedConfirm", { count: current.completedCount }),
        tone: "danger",
      }))
    ) {
      return;
    }
    const result = await clearCompletedOperationsFromBrowser();
    if (!result.ok) {
      notify.error(result.error || t("activityClearCompletedError"));
      return;
    }
    dismissActivityOperations(current.completed.map((item) => item.operationId));
    await refreshInbox();
  }, [refreshInbox, t]);

  const dismissTerminalOne = useCallback(
    async (operationId: string, terminalTab: "completed" | "failed") => {
      const result =
        terminalTab === "completed"
          ? await dismissCompletedOperationFromBrowser(operationId)
          : await dismissFailedOperationFromBrowser(operationId);
      if (!result.ok) return;
      dismissActivityOperations([operationId]);
      await refreshInbox();
    },
    [refreshInbox],
  );

  const forceStopOne = useCallback(
    async (item: OperationInboxItem) => {
      if (cancellingId) return;
      if (
        !(await confirm({
          title: t("activityForceStop"),
          description: t("activityForceStopConfirm"),
          confirmLabel: t("activityForceStop"),
          tone: "danger",
        }))
      ) {
        return;
      }
      setCancellingId(item.operationId);
      const result = await cancelOperationFromBrowser(item.serverId, item.operationId);
      setCancellingId(null);
      if (!result.ok) {
        notify.error(result.error || t("activityForceStopFailed"));
        return;
      }
      markActivityTerminal(item.operationId, "failed", result.data.message);
      await refreshInbox();
    },
    [cancellingId, refreshInbox, t],
  );

  return {
    cancellingId,
    clearFailed,
    clearCompleted,
    dismissTerminalOne,
    forceStopOne,
  };
}
