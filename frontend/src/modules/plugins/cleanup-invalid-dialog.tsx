"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { ExternalLink, LoaderCircle, Search, Trash2 } from "lucide-react";
import { syncMarketPluginReleasesAction } from "@/modules/plugins/actions";
import {
  cancelMarketReleaseSync,
  listMarketReleaseSyncs,
} from "@/modules/plugins/release-sync-actions";
import { trackReleaseSync } from "@/modules/plugins/release-sync-activity";
import {
  deletePluginsInBatches,
  isReleaseTaskActive,
  ISSUE_TONE,
  mergeReleaseTask,
  parseReleaseTask,
  RELEASE_TASK_ACTIVE,
} from "@/modules/plugins/release-sync-helpers";
import type { PluginFrameworkSection, ReleaseSyncJob } from "@/modules/plugins/types";
import { confirm, notify } from "@/shared/feedback";
import { randomId } from "@/shared/lib/random-id";
import { safeUrl } from "@/shared/lib/url";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function CleanupInvalidPluginsDialog({
  open,
  framework,
  onClose,
}: {
  open: boolean;
  framework: PluginFrameworkSection;
  onClose: () => void;
}) {
  const t = useTranslations("plugins.cleanupInvalid");
  const tRelease = useTranslations("plugins.bulkReleaseSync");
  const tPlugins = useTranslations("plugins");
  const router = useRouter();

  const [scope, setScope] = useState<"current" | "all">("current");
  const [tasks, setTasks] = useState<ReleaseSyncJob[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [uncheckedIds, setUncheckedIds] = useState<ReadonlySet<number>>(new Set());
  const [deletedIds, setDeletedIds] = useState<ReadonlySet<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void listMarketReleaseSyncs().then((result) => {
      if (!active || !result.ok) return;
      setTasks((current) => {
        const byId = new Map<string, ReleaseSyncJob>();
        for (const item of result.data) byId.set(item.operation_id, item);
        for (const item of current) {
          byId.set(item.operation_id, mergeReleaseTask(byId.get(item.operation_id), item));
        }
        return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
      });
    });
    return () => {
      active = false;
    };
  }, [open]);

  const currentTask = useMemo(() => {
    if (!tasks.length) return null;
    if (selectedTaskId) {
      const explicit = tasks.find((task) => task.operation_id === selectedTaskId);
      if (explicit) return explicit;
    }
    return (
      tasks.find((task) => isReleaseTaskActive(task)) ??
      tasks.find((task) => task.mode === "scan" || task.cleanable > 0) ??
      tasks[0] ??
      null
    );
  }, [selectedTaskId, tasks]);

  const activeOperationId = currentTask?.operation_id ?? null;
  const activeStatus = currentTask?.status ?? null;

  useEffect(() => {
    if (!open || !activeOperationId || !activeStatus || !RELEASE_TASK_ACTIVE.has(activeStatus)) {
      return;
    }
    const operationId = activeOperationId;
    return subscribeVisibleEventSource({
      url: `/ops-stream/plugin-releases/${encodeURIComponent(operationId)}`,
      eventTypes: ["snapshot"],
      lockName: `plugin-cleanup-scan-${operationId}`,
      onData: (raw) => {
        const next = parseReleaseTask(raw);
        if (!next) return;
        setTasks((current) => {
          const rest = current.filter((entry) => entry.operation_id !== operationId);
          const existing = current.find((entry) => entry.operation_id === operationId);
          return [mergeReleaseTask(existing, next), ...rest].sort((a, b) =>
            b.created_at.localeCompare(a.created_at),
          );
        });
      },
    });
  }, [activeOperationId, activeStatus, open]);

  const cleanableItems = useMemo(
    () =>
      (currentTask?.items ?? []).filter(
        (item) => item.cleanable && !deletedIds.has(item.plugin_id),
      ),
    [currentTask, deletedIds],
  );

  const advisoryItems = useMemo(
    () =>
      (currentTask?.items ?? []).filter(
        (item) =>
          !item.cleanable &&
          item.issue_code != null &&
          !deletedIds.has(item.plugin_id),
      ),
    [currentTask, deletedIds],
  );

  const selectedPluginIds = useMemo(
    () =>
      cleanableItems
        .filter((item) => !uncheckedIds.has(item.plugin_id))
        .map((item) => item.plugin_id),
    [cleanableItems, uncheckedIds],
  );

  const allChecked =
    cleanableItems.length > 0 && selectedPluginIds.length === cleanableItems.length;
  const running = currentTask ? isReleaseTaskActive(currentTask) : false;
  const percent = currentTask?.total
    ? Math.min(100, Math.round((currentTask.processed / currentTask.total) * 100))
    : currentTask?.status === "completed"
      ? 100
      : 0;

  function togglePlugin(pluginId: number, checked: boolean) {
    setUncheckedIds((current) => {
      const next = new Set(current);
      if (checked) next.delete(pluginId);
      else next.add(pluginId);
      return next;
    });
  }

  async function startScan() {
    setBusy(true);
    setError("");
    const id = (requestId.current ??= randomId());
    const result = await syncMarketPluginReleasesAction({
      requestId: id,
      mode: "scan",
      framework: scope === "all" ? null : framework,
    });
    setBusy(false);
    if (!result.ok) {
      setError(
        result.status === 403
          ? t("forbidden")
          : result.error || t("scanFailed"),
      );
      return;
    }
    requestId.current = null;
    setUncheckedIds(new Set());
    setSelectedTaskId(result.data.operation_id);
    setTasks((current) => [
      result.data,
      ...current.filter((entry) => entry.operation_id !== result.data.operation_id),
    ]);
    trackReleaseSync(result.data, { openTray: false });
  }

  async function cancelScan() {
    if (!currentTask || !running) return;
    setBusy(true);
    setError("");
    try {
      const result = await cancelMarketReleaseSync(currentTask.operation_id);
      if (!result.ok) {
        setError(result.error || tRelease("requestFailed"));
        return;
      }
      setTasks((current) => [
        result.data,
        ...current.filter((entry) => entry.operation_id !== result.data.operation_id),
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    if (!selectedPluginIds.length) return;
    if (
      !(await confirm({
        title: t("confirmTitle"),
        description: t("confirmDescription", { count: selectedPluginIds.length }),
        confirmLabel: t("deleteSelected", { count: selectedPluginIds.length }),
        tone: "danger",
      }))
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const outcome = await deletePluginsInBatches(selectedPluginIds);
      if (!outcome.ok) {
        setError(outcome.error || t("deleteFailed"));
        return;
      }
      setDeletedIds((current) => new Set([...current, ...selectedPluginIds]));
      notify.success(t("deleteSuccess", { count: outcome.deletedCount }));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      title={t("title")}
      closeLabel={t("close")}
      className="max-w-2xl"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-muted">{t("description")}</p>

        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface-raised/50 p-3">
          <div className="min-w-48 flex-1 space-y-1">
            <Label htmlFor="cleanup-invalid-scope">{t("scopeLabel")}</Label>
            <Select
              id="cleanup-invalid-scope"
              value={scope}
              disabled={busy || running}
              onChange={(event) => {
                setScope(event.target.value as "current" | "all");
                requestId.current = null;
              }}
            >
              <option value="current">
                {t("scopeCurrent", { framework: tPlugins(`frameworks.${framework}`) })}
              </option>
              <option value="all">{t("scopeAll")}</option>
            </Select>
          </div>
          {running ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy || currentTask?.cancel_requested}
              onClick={() => void cancelScan()}
            >
              {currentTask?.cancel_requested ? tRelease("cancelling") : tRelease("cancel")}
            </Button>
          ) : (
            <Button
              type="button"
              data-testid="market-cleanup-start-scan"
              disabled={busy}
              onClick={() => void startScan()}
            >
              {busy ? <LoaderCircle className="animate-spin" /> : <Search />}
              {t("startScan")}
            </Button>
          )}
        </div>

        {tasks.length > 1 ? (
          <div className="space-y-1">
            <Label htmlFor="cleanup-task-history">{t("recentTasks")}</Label>
            <Select
              id="cleanup-task-history"
              value={currentTask?.operation_id ?? ""}
              disabled={busy}
              onChange={(event) => {
                setSelectedTaskId(event.target.value);
                setUncheckedIds(new Set());
              }}
            >
              {tasks.slice(0, 10).map((task) => (
                <option key={task.operation_id} value={task.operation_id}>
                  {tRelease(`mode.${task.mode ?? "sync"}`)} ·{" "}
                  {task.framework ?? tRelease("allFrameworks")} ·{" "}
                  {tRelease("progress", { processed: task.processed, total: task.total })}
                </option>
              ))}
            </Select>
          </div>
        ) : null}

        {currentTask ? (
          <div className="space-y-3 rounded-lg border border-line p-3 text-sm">
            <div className="flex items-center justify-between gap-2" role="status">
              <span className="flex items-center gap-2 font-medium">
                {running ? (
                  <LoaderCircle className="size-4 animate-spin text-primary motion-reduce:animate-none" />
                ) : null}
                {tRelease(`mode.${currentTask.mode ?? "sync"}`)} ·{" "}
                {tRelease(`taskStatus.${currentTask.status}`)}
              </span>
              <span className="text-xs text-fg-muted">
                {tRelease("progress", {
                  processed: currentTask.processed,
                  total: currentTask.total,
                })}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
              <div
                className="h-full bg-primary transition-[width]"
                style={{ width: `${percent}%` }}
              />
            </div>
            {currentTask.current_plugin_title ? (
              <p className="break-all text-xs text-fg-muted">
                {tRelease("current", { name: currentTask.current_plugin_title })}
              </p>
            ) : null}
            {currentTask.status === "waiting" && currentTask.retry_at ? (
              <p className="text-xs text-warn">
                {tRelease("retryAt", {
                  time: new Date(currentTask.retry_at * 1000).toLocaleString(),
                })}
              </p>
            ) : null}
            <p className="text-xs text-fg-muted">
              {t("summary", {
                checked: currentTask.processed,
                cleanable: cleanableItems.length,
                advisory: advisoryItems.length,
              })}
            </p>

            {cleanableItems.length > 0 ? (
              <div className="space-y-2 pt-1">
                <div className="flex items-center justify-between gap-2">
                  <label className="inline-flex items-center gap-2 text-xs font-medium">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      disabled={busy}
                      className="size-4 rounded border-line accent-primary"
                      onChange={(event) =>
                        setUncheckedIds(
                          event.target.checked
                            ? new Set()
                            : new Set(cleanableItems.map((item) => item.plugin_id)),
                        )
                      }
                    />
                    {t("selectAllInvalid", { count: cleanableItems.length })}
                  </label>
                  <span className="text-xs text-fg-muted">
                    {t("selectedCount", { count: selectedPluginIds.length })}
                  </span>
                </div>
                <ul className="max-h-56 space-y-2 overflow-y-auto pr-1">
                  {cleanableItems.map((item) => {
                    const checked = !uncheckedIds.has(item.plugin_id);
                    const href = safeUrl(item.github_url);
                    return (
                      <li
                        key={item.plugin_id}
                        className="flex items-start justify-between gap-3 rounded-md border border-line p-2.5"
                      >
                        <label className="flex min-w-0 flex-1 items-start gap-2.5 text-xs">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={busy}
                            className="mt-0.5 size-4 shrink-0 rounded border-line accent-primary"
                            onChange={(event) => togglePlugin(item.plugin_id, event.target.checked)}
                          />
                          <span className="min-w-0 space-y-0.5">
                            <span className="block break-words font-medium text-fg">
                              {item.title}
                            </span>
                            <span className="block break-all text-fg-subtle">
                              {item.github_url}
                            </span>
                            {item.message ? (
                              <span className="block break-words text-fg-muted">
                                {item.message}
                              </span>
                            ) : null}
                          </span>
                        </label>
                        <div className="flex shrink-0 items-center gap-1.5">
                          {item.issue_code ? (
                            <Badge tone={ISSUE_TONE[item.issue_code]}>
                              {tRelease(`issue.${item.issue_code}`)}
                            </Badge>
                          ) : null}
                          {href ? (
                            <a
                              href={href}
                              target="_blank"
                              rel="noreferrer noopener"
                              aria-label={item.title}
                              className="text-fg-subtle hover:text-primary"
                            >
                              <ExternalLink className="size-3.5" />
                            </a>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : !running && currentTask.processed > 0 ? (
              <p className="rounded-md border border-line bg-surface-raised/40 px-3 py-2 text-xs text-fg-muted">
                {t("noneFound")}
              </p>
            ) : null}

            {advisoryItems.length > 0 ? (
              <div className="space-y-1.5 border-t border-line pt-2">
                <p className="text-xs font-medium text-fg-muted">
                  {t("advisoryTitle", { count: advisoryItems.length })}
                </p>
                <p className="text-xs text-fg-subtle">{t("advisoryHelp")}</p>
                <ul className="max-h-32 space-y-1.5 overflow-y-auto text-xs">
                  {advisoryItems.map((item) => (
                    <li
                      key={item.plugin_id}
                      className="flex items-start justify-between gap-2 rounded border border-line/70 px-2.5 py-1.5"
                    >
                      <span className="min-w-0 break-all">
                        <span className="font-medium">{item.title}</span>
                        {item.message ? (
                          <span className="block text-fg-subtle">{item.message}</span>
                        ) : null}
                      </span>
                      {item.issue_code ? (
                        <Badge tone={ISSUE_TONE[item.issue_code]}>
                          {tRelease(`issue.${item.issue_code}`)}
                        </Badge>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="rounded-lg border border-line bg-surface-raised/30 px-4 py-6 text-center text-sm text-fg-muted">
            {t("emptyPrompt")}
          </p>
        )}

        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {t("close")}
          </Button>
          <Button
            type="button"
            variant="danger"
            data-testid="market-cleanup-delete-selected"
            disabled={busy || running || selectedPluginIds.length === 0}
            onClick={() => void deleteSelected()}
          >
            {busy ? <LoaderCircle className="animate-spin" /> : <Trash2 />}
            {t("deleteSelected", { count: selectedPluginIds.length })}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
