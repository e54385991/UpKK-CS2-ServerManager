"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import type { PanelMonitorViewDto } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";

const EMPTY_GROUPS: NonNullable<PanelMonitorViewDto["error_groups"]> = [];

export function MonitorErrorCenter({
  groups,
  onFocus,
}: {
  groups?: PanelMonitorViewDto["error_groups"];
  onFocus?: (ts: string | null) => void;
}) {
  const t = useTranslations("settings.monitor");
  const items = groups ?? EMPTY_GROUPS;
  const [source, setSource] = useState("all");
  const [severity, setSeverity] = useState("all");
  const sources = useMemo(
    () => [...new Set(items.map((group) => group.source).filter((value): value is string => Boolean(value)))],
    [items],
  );
  const severities = useMemo(
    () => [...new Set(items.map((group) => group.severity).filter((value): value is string => Boolean(value)))],
    [items],
  );
  const visible = items.filter((group) => {
    if (source !== "all" && group.source !== source) return false;
    if (severity !== "all" && group.severity !== severity) return false;
    return true;
  });
  if (items.length === 0) {
    return (
      <p className="rounded-lg border border-line bg-surface-raised/40 px-4 py-6 text-sm text-fg-muted">
        {t("errorsEmpty")}
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <label className="flex items-center gap-2 text-xs text-fg-muted">
          {t("errorSource")}
          <select
            className="h-8 rounded-md border border-line bg-surface px-2 text-sm text-fg"
            value={source}
            data-testid="monitor-error-source"
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="all">{t("filterAll")}</option>
            {sources.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-fg-muted">
          {t("filterSeverity")}
          <select
            className="h-8 rounded-md border border-line bg-surface px-2 text-sm text-fg"
            value={severity}
            data-testid="monitor-error-severity"
            onChange={(event) => setSeverity(event.target.value)}
          >
            <option value="all">{t("filterAll")}</option>
            {severities.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="bg-surface-raised text-xs uppercase tracking-wide text-fg-subtle">
            <tr>
              <th className="px-3 py-2 font-medium">{t("errorSource")}</th>
              <th className="px-3 py-2 font-medium">{t("errorCount")}</th>
              <th className="px-3 py-2 font-medium">{t("errorSummary")}</th>
              <th className="px-3 py-2 font-medium">{t("errorIds")}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((group, index) => (
              <tr
                key={`${group.source}-${group.error_code}-${group.route}-${index}`}
                className={cn("border-t border-line", group.severity === "critical" && "bg-danger-muted/30")}
              >
                <td className="px-3 py-2 align-top">
                  <button
                    type="button"
                    className="text-left text-fg hover:text-primary"
                    onClick={() => onFocus?.(group.last_ts ?? null)}
                  >
                    <span className="block font-medium">{group.source ?? "—"}</span>
                    <span className="text-xs text-fg-subtle">{group.route ?? group.error_code}</span>
                  </button>
                </td>
                <td className="px-3 py-2 align-top tabular-nums">{group.count}</td>
                <td className="px-3 py-2 align-top text-fg-muted">
                  <p>{group.summary}</p>
                  {group.frames?.[0] ? (
                    <p className="mt-1 font-mono text-[11px] text-fg-subtle">
                      {group.frames[0].file}:{group.frames[0].line} {group.frames[0].function}
                    </p>
                  ) : null}
                </td>
                <td className="px-3 py-2 align-top font-mono text-[11px] text-fg-subtle">
                  {group.request_id ? <div>req {group.request_id}</div> : null}
                  {group.operation_id ? <div>op {group.operation_id}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
