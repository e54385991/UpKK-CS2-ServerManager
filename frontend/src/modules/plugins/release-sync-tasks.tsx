"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { Eraser, LoaderCircle, Trash2, X } from "lucide-react";
import type { ReleaseSyncJob } from "@/modules/plugins/types";
import {
  cancelMarketReleaseSync,
  deleteMarketReleaseSync,
} from "@/modules/plugins/release-sync-actions";
import { latestSubmittedReleaseSync } from "@/modules/plugins/release-sync-activity";
import {
  deletePluginsInBatches,
  isReleaseTaskActive,
  ISSUE_TONE,
  mergeReleaseTask,
  parseReleaseTask,
  RELEASE_TASK_ACTIVE,
  RELEASE_TASK_TERMINAL,
} from "@/modules/plugins/release-sync-helpers";
import { confirm, notify } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";

const ACTION_TONE = {
  updated: "ok",
  unchanged: "neutral",
  skipped: "info",
  failed: "danger",
} as const;

export function ReleaseSyncTasks({
  initialTasks,
}: {
  initialTasks: readonly ReleaseSyncJob[];
}) {
  const t = useTranslations("plugins.bulkReleaseSync");
  const router = useRouter();
  const submitted = latestSubmittedReleaseSync();
  const [updates, setUpdates] = useState<ReleaseSyncJob[]>([]);
  const [removedIds, setRemovedIds] = useState<readonly string[]>([]);
  const [cleanedPluginIds, setCleanedPluginIds] = useState<readonly number[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    () => submitted?.operation_id ?? initialTasks[0]?.operation_id ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refreshed = useRef(new Set<string>());
  const removed = useMemo(() => new Set(removedIds), [removedIds]);
  const cleanedSet = useMemo(() => new Set(cleanedPluginIds), [cleanedPluginIds]);

  const tasks = useMemo(() => {
    const byId = new Map<string, ReleaseSyncJob>();
    if (submitted) byId.set(submitted.operation_id, submitted);
    for (const task of initialTasks) byId.set(task.operation_id, task);
    for (const task of updates) {
      byId.set(task.operation_id, mergeReleaseTask(byId.get(task.operation_id), task));
    }
    return [...byId.values()]
      .filter((task) => !removed.has(task.operation_id))
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
  }, [initialTasks, removed, submitted, updates]);

  const selected = tasks.find((task) => task.operation_id === selectedId) ?? tasks[0] ?? null;
  const selectedOperationId = selected?.operation_id ?? null;
  const selectedStatus = selected?.status ?? null;
  const selectedItems = useMemo(() => selected?.items ?? [], [selected]);
  const cleanableIds = useMemo(
    () =>
      selectedItems
        .filter((item) => item.cleanable && !cleanedSet.has(item.plugin_id))
        .map((item) => item.plugin_id),
    [cleanedSet, selectedItems],
  );

  useEffect(() => {
    const onSubmit = (event: Event) => {
      const task = (event as CustomEvent<ReleaseSyncJob>).detail;
      setUpdates((current) => [
        ...current.filter((entry) => entry.operation_id !== task.operation_id),
        task,
      ]);
      setSelectedId(task.operation_id);
    };
    window.addEventListener("plugin-release-sync-submitted", onSubmit);
    return () => window.removeEventListener("plugin-release-sync-submitted", onSubmit);
  }, []);

  useEffect(() => {
    if (!selectedOperationId || !selectedStatus || !RELEASE_TASK_ACTIVE.has(selectedStatus)) {
      return;
    }
    const operationId = selectedOperationId;
    return subscribeVisibleEventSource({
      url: `/ops-stream/plugin-releases/${encodeURIComponent(operationId)}`,
      eventTypes: ["snapshot"],
      lockName: `plugin-release-sync-${operationId}`,
      onData: (raw) => {
        const next = parseReleaseTask(raw);
        if (!next) return;
        setUpdates((current) => [
          ...current.filter((task) => task.operation_id !== operationId),
          next,
        ]);
      },
    });
  }, [selectedOperationId, selectedStatus]);

  useEffect(() => {
    if (
      !selected ||
      !RELEASE_TASK_TERMINAL.has(selected.status) ||
      refreshed.current.has(selected.operation_id)
    ) {
      return;
    }
    refreshed.current.add(selected.operation_id);
    router.refresh();
  }, [router, selected]);

  async function cancelSelected() {
    if (!selected || !isReleaseTaskActive(selected)) return;
    setBusy(true);
    setError("");
    try {
      const result = await cancelMarketReleaseSync(selected.operation_id);
      if (!result.ok) {
        setError(result.error || t("requestFailed"));
        return;
      }
      setUpdates((current) => [
        ...current.filter((task) => task.operation_id !== result.data.operation_id),
        result.data,
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    if (!selected || isReleaseTaskActive(selected)) return;
    setBusy(true);
    setError("");
    try {
      const result = await deleteMarketReleaseSync(selected.operation_id);
      if (!result.ok) {
        setError(result.error || t("requestFailed"));
        return;
      }
      setRemovedIds((current) => [...new Set([...current, selected.operation_id])]);
      setSelectedId(null);
    } finally {
      setBusy(false);
    }
  }

  async function clearTerminal() {
    const terminal = tasks.filter((task) => RELEASE_TASK_TERMINAL.has(task.status));
    if (terminal.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const deleted = new Set<string>();
      for (const task of terminal) {
        const result = await deleteMarketReleaseSync(task.operation_id);
        if (result.ok) deleted.add(task.operation_id);
      }
      setRemovedIds((current) => [...new Set([...current, ...deleted])]);
      if (selected && deleted.has(selected.operation_id)) setSelectedId(null);
    } finally {
      setBusy(false);
    }
  }

  async function cleanSelectedInvalid() {
    if (!cleanableIds.length) return;
    if (
      !(await confirm({
        title: t("cleanInvalidTitle"),
        description: t("cleanInvalidConfirm", { count: cleanableIds.length }),
        confirmLabel: t("cleanInvalid", { count: cleanableIds.length }),
        tone: "danger",
      }))
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const outcome = await deletePluginsInBatches(cleanableIds);
      if (!outcome.ok) {
        setError(outcome.error || t("requestFailed"));
        return;
      }
      setCleanedPluginIds((current) => [...new Set([...current, ...cleanableIds])]);
      notify.success(t("cleanInvalidSuccess", { count: outcome.deletedCount }));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  const percent = selected?.total
    ? Math.min(100, Math.round((selected.processed / selected.total) * 100))
    : selected?.status === "completed"
      ? 100
      : 0;

  return (
    <section
      data-testid="release-sync-tasks"
      className="max-h-96 overflow-y-auto border-b border-line p-4 text-sm"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-semibold">{t("taskList")}</h3>
        {tasks.some((task) => RELEASE_TASK_TERMINAL.has(task.status)) ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => void clearTerminal()}
          >
            <Eraser className="size-3.5" />
            {t("clearTerminal")}
          </Button>
        ) : null}
      </div>
      {tasks.length === 0 ? <p className="py-3 text-xs text-fg-muted">{t("noTasks")}</p> : null}
      <div className="space-y-2">
        {tasks.slice(0, 20).map((task) => (
          <button
            key={task.operation_id}
            type="button"
            className={`block w-full rounded border p-2 text-left hover:bg-surface-raised ${
              selected?.operation_id === task.operation_id ? "border-primary" : "border-line"
            }`}
            onClick={() => setSelectedId(task.operation_id)}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="truncate font-medium">
                {t(`mode.${task.mode ?? "sync"}`)} · {task.framework ?? t("allFrameworks")}
              </span>
              <Badge
                tone={
                  task.status === "failed"
                    ? "danger"
                    : task.status === "waiting"
                      ? "warn"
                      : "neutral"
                }
              >
                {t(`taskStatus.${task.status}`)}
              </Badge>
            </span>
            <p className="truncate text-xs text-fg-muted">{task.message}</p>
          </button>
        ))}
      </div>

      {selected ? (
        <div className="mt-3 space-y-2 rounded border border-line p-3">
          <div className="flex items-center justify-between gap-2" role="status">
            <span className="flex items-center gap-2">
              {isReleaseTaskActive(selected) ? (
                <LoaderCircle className="size-3.5 animate-spin text-primary motion-reduce:animate-none" />
              ) : null}
              {t(`taskStatus.${selected.status}`)}
            </span>
            <span className="text-xs text-fg-muted">
              {t("progress", { processed: selected.processed, total: selected.total })}
            </span>
          </div>
          <div
            className="h-1.5 overflow-hidden rounded-full bg-surface-raised"
            aria-label={t("progress", { processed: selected.processed, total: selected.total })}
          >
            <div
              className="h-full bg-primary transition-[width]"
              style={{ width: `${percent}%` }}
            />
          </div>
          {selected.current_plugin_title ? (
            <p className="break-all text-xs text-fg-muted">
              {t("current", { name: selected.current_plugin_title })}
            </p>
          ) : null}
          {selected.status === "waiting" && selected.retry_at ? (
            <p className="text-xs text-warn">
              {t("retryAt", { time: new Date(selected.retry_at * 1000).toLocaleString() })}
            </p>
          ) : null}
          <p className="text-xs">{selected.message}</p>
          <p className="text-xs text-fg-muted">
            {t("results", {
              updated: selected.updated,
              unchanged: selected.unchanged,
              skipped: selected.skipped,
              failed: selected.failed,
              cleanable: selected.cleanable,
            })}
          </p>
          {selectedItems.length > 0 ? (
            <ul className="max-h-36 space-y-1.5 overflow-y-auto text-xs">
              {selectedItems
                .slice()
                .reverse()
                .slice(0, 30)
                .map((item) => (
                  <li key={item.plugin_id} className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-all">
                      {item.title}
                      {item.tag ? ` (${item.tag})` : ""}
                      {item.message ? (
                        <span className="block text-fg-subtle">{item.message}</span>
                      ) : null}
                    </span>
                    <span className="flex shrink-0 flex-wrap justify-end gap-1">
                      {item.issue_code ? (
                        <Badge tone={ISSUE_TONE[item.issue_code]}>
                          {t(`issue.${item.issue_code}`)}
                        </Badge>
                      ) : (
                        <Badge tone={ACTION_TONE[item.action]}>
                          {t(`action.${item.action}`)}
                        </Badge>
                      )}
                    </span>
                  </li>
                ))}
            </ul>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {isReleaseTaskActive(selected) ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy || selected.cancel_requested}
                onClick={() => void cancelSelected()}
              >
                {selected.cancel_requested ? t("cancelling") : t("cancel")}
              </Button>
            ) : (
              <>
                {cleanableIds.length > 0 ? (
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    data-testid="release-sync-clean-invalid"
                    disabled={busy}
                    onClick={() => void cleanSelectedInvalid()}
                  >
                    <Trash2 className="size-3.5" />
                    {t("cleanInvalid", { count: cleanableIds.length })}
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => void deleteSelected()}
                >
                  <X className="size-3.5" />
                  {t("deleteTask")}
                </Button>
              </>
            )}
          </div>
          {error ? (
            <p role="alert" className="text-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
