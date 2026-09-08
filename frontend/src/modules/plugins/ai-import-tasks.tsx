"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import type { components } from "@/shared/api/schema";
import {
  cancelAIImport,
  clearCompletedAIImports,
  deleteAIImport,
  getAIImport,
  listAIImports,
} from "@/modules/plugins/ai-import-actions";
import { AIImportUsage } from "@/modules/plugins/ai-import-usage";
import { latestSubmittedAIImport } from "@/modules/plugins/ai-import-activity";
import { Button } from "@/shared/ui/button";
import { confirm, notify } from "@/shared/feedback";
import { Eraser, LoaderCircle } from "lucide-react";

type Task = components["schemas"]["PluginAIImportView"];
const active = (task: Task) => task.status === "queued" || task.status === "running";
const completed = (task: Task) => task.status === "completed" || task.status === "cancelled";
const queueVisible = (task: Task) => active(task) || task.status === "failed";

export function AIImportTasks({ initialTasks }: { initialTasks: readonly Task[] }) {
  const t = useTranslations("plugins.aiImport");
  const statusLabel = (value: string) => {
    const key = (["queued", "running", "completed", "cancelled", "failed", "imported", "skipped"] as const).find(key => key === value);
    return key ? t(`status.${key}`) : value;
  };
  const phaseLabel = (value: string) => {
    const key = (["queued", "starting", "searching", "reading", "analyzing", "importing", "skipped", "failed_item", "completed", "stopped", "rate_limited", "failed", "cancelled"] as const).find(key => key === value);
    return key ? t(`phase.${key}`) : value;
  };
  const [tasks, setTasks] = useState<Task[]>([...initialTasks]);
  const [selected, setSelected] = useState<Task | null>(() => latestSubmittedAIImport() ?? initialTasks[0] ?? null);
  const [tab, setTab] = useState<"queue" | "completed">(
    initialTasks.some(queueVisible) ? "queue" : "completed",
  );
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    const refresh = () => { void listAIImports().then(result => { if (mounted && result.ok) { setTasks(result.data); setSelected(current => result.data.find(task => task.operation_id === current?.operation_id) ?? result.data[0] ?? null); } }); };
    const onSubmit = (event: Event) => {
      const task = (event as CustomEvent<Task>).detail;
      setTasks(current => [task, ...current.filter(item => item.operation_id !== task.operation_id)]);
      setSelected(task); refresh();
    };
    refresh();
    window.addEventListener("plugin-ai-import-submitted", onSubmit);
    // The tray's one-click "clear failed" deletes failed import jobs too;
    // re-read immediately instead of leaving them on screen for a poll cycle.
    window.addEventListener("plugin-ai-import-refresh", refresh);
    const timer = window.setInterval(refresh, 5000);
    return () => { mounted = false; clearInterval(timer); window.removeEventListener("plugin-ai-import-submitted", onSubmit); window.removeEventListener("plugin-ai-import-refresh", refresh); };
  }, [initialTasks.length]);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const selectedId = selected?.operation_id;
  const selectedActive = selected ? active(selected) : false;
  useEffect(() => {
    if (!selectedId || !selectedActive) return;
    const stream = new EventSource(`/ops-stream/plugin-imports/${encodeURIComponent(selectedId)}`);
    const receive = (event: MessageEvent<string>) => {
      try {
        const task = JSON.parse(event.data) as Task;
        setSelected(task);
        setTasks(current => current.map(item => item.operation_id === task.operation_id ? task : item));
        if (!active(task)) stream.close();
      } catch { setError(t("requestFailed")); }
    };
    stream.addEventListener("snapshot", receive);
    return () => stream.close();
  }, [selectedId, selectedActive, t]);
  const activeTasks = tasks.filter(queueVisible);
  const completedTasks = tasks.filter(completed);
  const visibleTasks = tab === "queue" ? activeTasks : completedTasks;
  async function clearCompleted() {
    if (!completedTasks.length) return;
    if (!(await confirm({
      title: t("clearCompletedTitle"),
      description: t("clearCompletedConfirm", { count: completedTasks.length }),
      confirmLabel: t("clearCompleted"),
      tone: "danger",
    }))) return;
    setDeleting(true);
    const result = await clearCompletedAIImports();
    setDeleting(false);
    if (!result.ok) {
      notify.error(result.error || t("requestFailed"));
      return;
    }
    setTasks(current => current.filter(task => !completed(task)));
    setSelected(current => current && completed(current) ? null : current);
    notify.success(result.data.message || t("clearCompletedSuccess"));
    window.dispatchEvent(new Event("plugin-ai-import-refresh"));
  }
  return <section className="max-h-80 overflow-y-auto border-b border-line p-4 text-sm">
    <div className="mb-2 flex items-center justify-between gap-2">
      <h3 className="font-semibold">{t("tasks")}</h3>
      {tab === "completed" && completedTasks.length > 0 ? <Button type="button" variant="ghost" size="sm" disabled={deleting} onClick={() => void clearCompleted()}><Eraser className="size-3.5" />{t("clearCompleted")}</Button> : null}
    </div>
    <div className="mb-2 flex rounded-md border border-line bg-surface-raised p-0.5">
      {(["queue", "completed"] as const).map((value) => {
        const count = value === "queue" ? activeTasks.length : completedTasks.length;
        return <button key={value} type="button" className={`flex-1 rounded px-2 py-1 text-xs ${tab === value ? "bg-surface text-fg shadow-sm" : "text-fg-muted"}`} onClick={() => { setTab(value); setSelected(current => (current && (value === "queue" ? queueVisible(current) : completed(current)) ? current : (value === "queue" ? activeTasks[0] : completedTasks[0]) ?? null)); }}>
          {value === "queue" ? t("queueTab") : t("completedTab")} {count > 0 ? `(${count})` : ""}
        </button>;
      })}
    </div>
    {visibleTasks.length === 0 ? <p className="py-3 text-xs text-fg-muted">{tab === "queue" ? t("queueEmpty") : t("completedEmpty")}</p> : null}
    <div className="space-y-2">{visibleTasks.slice(0, 20).map(task => <button key={task.operation_id} className="block w-full rounded border border-line p-2 text-left hover:bg-surface-raised" onClick={() => { setSelected(task); void getAIImport(task.operation_id).then(r => { if (r.ok) setSelected(r.data); }); }}>
      <span className="font-medium">{task.options.framework === "all" ? t("allFrameworks") : task.options.framework}</span><span className="ml-2 text-fg-muted">{statusLabel(task.status)}</span>
      <p className="truncate text-xs text-fg-muted">{task.message}</p>
    </button>)}</div>
    {selected && <div className="mt-3 space-y-2 rounded border border-line p-3">
      <p className="flex items-center gap-2" role="status">{active(selected) && <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin text-primary motion-reduce:animate-none" />}{phaseLabel(selected.phase)} · {statusLabel(selected.status)}</p>
      <AIImportUsage task={selected} />
      <p>{t("elapsed", { seconds: selected.started_at ? Math.max(0, Math.floor(((selected.completed_at ? Date.parse(selected.completed_at) : clock) - Date.parse(selected.started_at)) / 1000)) : 0 })}</p>
      <p className="break-all text-xs">{selected.current_repository}</p>
      <p className="text-xs">{t("results", { imported: selected.items.filter(i => i.status === "imported").length, skipped: selected.items.filter(i => i.status === "skipped").length, failed: selected.items.filter(i => i.status === "failed").length })}</p>
      <p className="text-xs">{selected.message}</p>
      {selected.retry_at && <p>{t("retryAt")}: {new Date(selected.retry_at * 1000).toLocaleString()}</p>}
      {active(selected) && <Button variant="outline" size="sm" disabled={selected.cancel_requested} onClick={() => { void cancelAIImport(selected.operation_id).then(result => { if (result.ok) setSelected(result.data); else setError(t("requestFailed")); }); }}>{t("cancel")}</Button>}
      {!active(selected) && <Button variant="outline" size="sm" disabled={deleting} onClick={async () => {
        const id = selected.operation_id;
        setDeleting(true); setError("");
        try {
          const result = await deleteAIImport(id);
          if (result.ok) {
            setTasks(current => current.filter(task => task.operation_id !== id));
            setSelected(current => current?.operation_id === id ? null : current);
            window.dispatchEvent(new Event("plugin-ai-import-refresh"));
          } else setError(t("requestFailed"));
        } finally { setDeleting(false); }
      }}>{deleting ? <LoaderCircle className="animate-spin" /> : null}{t("deleteTask")}</Button>}
      <ul className="max-h-36 overflow-auto text-xs">{selected.items.map((item,index) => <li key={index} className="mb-2 break-all">{item.repository} · {t(`status.${item.status}`)}<p>{item.message}</p></li>)}</ul>
      <pre className="max-h-28 overflow-auto whitespace-pre-wrap text-xs text-fg-muted">{selected.events.filter(event => !event.token_usage).map(event => event.message).join("\n")}</pre>
    </div>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </section>;
}
