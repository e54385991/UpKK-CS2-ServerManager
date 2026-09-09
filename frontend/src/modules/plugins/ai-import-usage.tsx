"use client";

import { useTranslations } from "next-intl";
import { LoaderCircle } from "lucide-react";
import type { components } from "@/shared/api/schema";

type Task = components["schemas"]["PluginAIImportView"];

export function AIImportUsage({ task }: { task: Task }) {
  const t = useTranslations("plugins.aiImport.usage");
  const usage = [...task.events].reverse().find(event => event.token_usage)?.token_usage;
  const running = task.status === "running";
  if (!usage) return null;
  const streaming = running && usage.stage !== "completed" && ["analyzing", "filtering"].includes(task.phase);
  return <div className="rounded-md border border-line bg-surface-raised p-3" data-testid="ai-import-usage">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span className="flex items-center gap-2" role="status">
        {streaming && <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin text-primary motion-reduce:animate-none" />}
        {t(streaming ? usage.stage : "lastRequest")}
      </span>
      <span className="text-fg-muted">{t(usage.estimated ? "estimated" : "reported")}</span>
    </div>
    <dl className="mt-2 grid grid-cols-3 gap-2 tabular-nums">
      {(["input", "output", "reasoning"] as const).map((key) => <div key={key}>
        <dt className="text-xs text-fg-muted">{t(key)}</dt>
        <dd className="text-sm font-semibold" data-testid={`ai-tokens-${key}`}>{new Intl.NumberFormat().format(usage[`${key}_tokens`])}</dd>
      </div>)}
    </dl>
    <p className="mt-2 text-xs text-fg-muted">{t("hint")}</p>
  </div>;
}
