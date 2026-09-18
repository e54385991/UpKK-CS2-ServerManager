"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Eraser, LoaderCircle, X } from "lucide-react";
import type { DescriptionSyncJob } from "@/modules/plugins/types";
import {
  cancelMarketDescriptionSync,
  deleteMarketDescriptionSync,
} from "@/modules/plugins/description-sync-actions";
import { latestSubmittedDescriptionSync } from "@/modules/plugins/description-sync-activity";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";

const ACTIVE = new Set(["queued", "running", "waiting"]);
const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const ACTION_TONE = {
  updated: "ok",
  unchanged: "neutral",
  skipped: "info",
  failed: "danger",
} as const;

function isActive(task: DescriptionSyncJob): boolean {
  return ACTIVE.has(task.status);
}

function statusRank(task: DescriptionSyncJob): number {
  if (TERMINAL.has(task.status)) return 3;
  return 1;
}

function mergeTask(
  current: DescriptionSyncJob | undefined,
  candidate: DescriptionSyncJob,
): DescriptionSyncJob {
  if (!current) return candidate;
  if (statusRank(current) === 3 && statusRank(candidate) < 3) return current;
  if (statusRank(candidate) === 3) return candidate;
  if (candidate.processed >= current.processed) return candidate;
  return current;
}

function parseTask(raw: string): DescriptionSyncJob | null {
  try {
    const value = JSON.parse(raw) as Partial<DescriptionSyncJob>;
    if (
      typeof value.operation_id !== "string" ||
      typeof value.status !== "string" ||
      typeof value.processed !== "number" ||
      typeof value.total !== "number"
    ) {
      return null;
    }
    return value as DescriptionSyncJob;
  } catch {
    return null;
  }
}

export function DescriptionSyncTasks({
  initialTasks,
}: {
  initialTasks: readonly DescriptionSyncJob[];
}) {
  const t = useTranslations("plugins.sync");
  const router = useRouter();
  const submitted = latestSubmittedDescriptionSync();
  const [updates, setUpdates] = useState<DescriptionSyncJob[]>([]);
  const [removedIds, setRemovedIds] = useState<readonly string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    () => submitted?.operation_id ?? initialTasks[0]?.operation_id ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refreshed = useRef(new Set<string>());
  const removed = useMemo(() => new Set(removedIds), [removedIds]);
  const tasks = useMemo(() => {
    const byId = new Map<string, DescriptionSyncJob>();
    if (submitted) byId.set(submitted.operation_id, submitted);
    for (const task of initialTasks) byId.set(task.operation_id, task);
    for (const task of updates) byId.set(task.operation_id, mergeTask(byId.get(task.operation_id), task));
    return [...byId.values()]
      .filter((task) => !removed.has(task.operation_id))
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
  }, [initialTasks, removed, submitted, updates]);
  const selected = tasks.find((task) => task.operation_id === selectedId) ?? tasks[0] ?? null;
  const selectedOperationId = selected?.operation_id ?? null;
  const selectedStatus = selected?.status ?? null;
  const selectedItems = selected?.items ?? [];

  useEffect(() => {
    if (!selectedOperationId || !selectedStatus || !ACTIVE.has(selectedStatus)) return;
    const operationId = selectedOperationId;
    return subscribeVisibleEventSource({
      url: `/ops-stream/plugin-descriptions/${encodeURIComponent(operationId)}`,
      eventTypes: ["snapshot"],
      lockName: `plugin-description-sync-${operationId}`,
      onData: (raw) => {
        const next = parseTask(raw);
        if (!next) return;
        setUpdates((current) => [
          ...current.filter((task) => task.operation_id !== operationId),
          next,
        ]);
      },
    });
  }, [selectedOperationId, selectedStatus]);

  useEffect(() => {
    if (!selected || !TERMINAL.has(selected.status) || refreshed.current.has(selected.operation_id)) {
      return;
    }
    refreshed.current.add(selected.operation_id);
    router.refresh();
  }, [router, selected]);

  async function cancelSelected() {
    if (!selected || !isActive(selected)) return;
    setBusy(true);
    setError("");
    try {
      const result = await cancelMarketDescriptionSync(selected.operation_id);
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
    if (!selected || isActive(selected)) return;
    setBusy(true);
    setError("");
    try {
      const result = await deleteMarketDescriptionSync(selected.operation_id);
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
    const terminal = tasks.filter((task) => TERMINAL.has(task.status));
    if (terminal.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const removed = new Set<string>();
      for (const task of terminal) {
        const result = await deleteMarketDescriptionSync(task.operation_id);
        if (result.ok) removed.add(task.operation_id);
      }
      setRemovedIds((current) => [...new Set([...current, ...removed])]);
      if (selected && removed.has(selected.operation_id)) setSelectedId(null);
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
    <section className="max-h-96 overflow-y-auto border-b border-line p-4 text-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-semibold">{t("taskList")}</h3>
        {tasks.some((task) => TERMINAL.has(task.status)) ? (
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
              <span className="truncate font-medium">{task.framework ?? t("allFrameworks")}</span>
              <Badge tone={task.status === "failed" ? "danger" : task.status === "waiting" ? "warn" : "neutral"}>
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
              {isActive(selected) ? (
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
            <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
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
            })}
          </p>
          {selectedItems.length > 0 ? (
            <ul className="max-h-32 space-y-1 overflow-y-auto text-xs">
              {selectedItems
                .slice()
                .reverse()
                .slice(0, 20)
                .map((item) => (
                  <li key={item.plugin_id} className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-all">
                      {item.title}
                      {item.message ? <span className="block text-fg-subtle">{item.message}</span> : null}
                    </span>
                    <Badge tone={ACTION_TONE[item.action]}>{t(`action.${item.action}`)}</Badge>
                  </li>
                ))}
            </ul>
          ) : null}
          <div className="flex gap-2">
            {isActive(selected) ? (
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
            )}
          </div>
          {error ? <p role="alert" className="text-danger">{error}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
