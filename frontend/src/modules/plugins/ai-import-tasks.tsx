"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import type { components } from "@/shared/api/schema";
import { cancelAIImport, getAIImport, listAIImports } from "@/modules/plugins/ai-import-actions";
import { latestSubmittedAIImport } from "@/modules/plugins/ai-import-activity";
import { Button } from "@/shared/ui/button";

type Task = components["schemas"]["PluginAIImportView"];
const active = (task: Task) => task.status === "queued" || task.status === "running";

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
    const timer = window.setInterval(refresh, 5000);
    return () => { mounted = false; clearInterval(timer); window.removeEventListener("plugin-ai-import-submitted", onSubmit); };
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
  if (!tasks.length) return null;
  return <section className="max-h-80 overflow-y-auto border-b border-line p-4 text-sm">
    <h3 className="mb-2 font-semibold">{t("tasks")}</h3>
    <div className="space-y-2">{tasks.slice(0, 20).map(task => <button key={task.operation_id} className="block w-full rounded border border-line p-2 text-left hover:bg-surface-raised" onClick={() => { setSelected(task); void getAIImport(task.operation_id).then(r => { if (r.ok) setSelected(r.data); }); }}>
      <span className="font-medium">{task.options.framework === "all" ? t("allFrameworks") : task.options.framework}</span><span className="ml-2 text-fg-muted">{statusLabel(task.status)}</span>
      <p className="truncate text-xs text-fg-muted">{task.message}</p>
    </button>)}</div>
    {selected && <div className="mt-3 space-y-2 rounded border border-line p-3">
      <p>{phaseLabel(selected.phase)} · {statusLabel(selected.status)}</p>
      <p>{t("elapsed", { seconds: selected.started_at ? Math.max(0, Math.floor(((selected.completed_at ? Date.parse(selected.completed_at) : clock) - Date.parse(selected.started_at)) / 1000)) : 0 })}</p>
      <p className="break-all text-xs">{selected.current_repository}</p>
      <p className="text-xs">{t("results", { imported: selected.items.filter(i => i.status === "imported").length, skipped: selected.items.filter(i => i.status === "skipped").length, failed: selected.items.filter(i => i.status === "failed").length })}</p>
      <p className="text-xs">{selected.message}</p>
      {selected.retry_at && <p>{t("retryAt")}: {new Date(selected.retry_at * 1000).toLocaleString()}</p>}
      {active(selected) && <Button variant="outline" size="sm" disabled={selected.cancel_requested} onClick={() => { void cancelAIImport(selected.operation_id).then(result => { if (result.ok) setSelected(result.data); else setError(t("requestFailed")); }); }}>{t("cancel")}</Button>}
      <ul className="max-h-36 overflow-auto text-xs">{selected.items.map((item,index) => <li key={index} className="mb-2 break-all">{item.repository} · {t(`status.${item.status}`)}<p>{item.message}</p></li>)}</ul>
      <pre className="max-h-28 overflow-auto whitespace-pre-wrap text-xs text-fg-muted">{selected.events.map(event => event.message).join("\n")}</pre>
    </div>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </section>;
}
