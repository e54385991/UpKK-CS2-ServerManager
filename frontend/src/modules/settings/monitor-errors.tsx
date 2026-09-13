"use client";

import { useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { getMonitorErrorsAction } from "@/modules/settings/actions";
import {
  formatErrorSource,
  formatMonitorInstant,
  type MonitorTranslate,
} from "@/modules/settings/monitor-format";
import {
  discardedErrorCount,
  matchesErrorFilter,
  uniqueNonEmpty,
} from "@/modules/settings/monitor-logic";
import type { MonitorRange } from "@/modules/settings/monitor-types";
import type { PanelErrorListViewDto, PanelMonitorViewDto } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

const EMPTY_GROUPS: NonNullable<PanelMonitorViewDto["error_groups"]> = [];
const EMPTY_DETAILS: NonNullable<PanelErrorListViewDto["items"]> = [];

export function MonitorErrorCenter({
  range,
  instanceId,
  groups,
  dropped,
  truncated,
  refreshEpoch,
  onFocus,
}: {
  range: MonitorRange;
  instanceId?: string;
  groups?: PanelMonitorViewDto["error_groups"];
  dropped?: number;
  truncated?: number;
  refreshEpoch: number;
  onFocus?: (ts: string | null) => void;
}) {
  const t = useTranslations("settings.monitor");
  const locale = useLocale();
  const translate = t as unknown as MonitorTranslate;
  const summaries = groups ?? EMPTY_GROUPS;
  const [source, setSource] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [items, setItems] = useState(EMPTY_DETAILS);
  const [cursor, setCursor] = useState<string | null>(null);
  const [listedDropped, setDropped] = useState(0);
  const [listedTruncated, setTruncated] = useState(false);
  const [pending, setPending] = useState<"load" | "more" | null>("load");
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await getMonitorErrorsAction({
        range,
        instanceId,
        source: source === "all" ? undefined : source,
        severity: severity === "all" ? undefined : severity,
      });
      if (cancelled) return;
      if (!result.ok) {
        setItems(EMPTY_DETAILS);
        setCursor(null);
        setPending(null);
        setFetchError(t("errorsFetchError", { status: result.status || "network" }));
        return;
      }
      setItems(result.data.items ?? EMPTY_DETAILS);
      setCursor(result.data.next_cursor ?? null);
      setDropped(result.data.dropped ?? 0);
      setTruncated(Boolean(result.data.truncated));
      setFetchError(null);
      setPending(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [instanceId, range, refreshEpoch, severity, source, t]);

  async function loadMore() {
    if (!cursor || pending !== null) return;
    setPending("more");
    const result = await getMonitorErrorsAction({
      range,
      instanceId,
      source: source === "all" ? undefined : source,
      severity: severity === "all" ? undefined : severity,
      cursor,
    });
    if (!result.ok) {
      setPending(null);
      setFetchError(t("errorsFetchError", { status: result.status || "network" }));
      return;
    }
    setItems((current) => [...current, ...(result.data.items ?? EMPTY_DETAILS)]);
    setCursor(result.data.next_cursor ?? null);
    setDropped(result.data.dropped ?? 0);
    setTruncated(Boolean(result.data.truncated));
    setPending(null);
  }

  const sources = useMemo(
    () => uniqueNonEmpty([...summaries.map((group) => group.source), ...items.map((item) => item.source)]),
    [items, summaries],
  );
  const severities = useMemo(
    () => uniqueNonEmpty([...summaries.map((group) => group.severity), ...items.map((item) => item.severity)]),
    [items, summaries],
  );
  const visibleGroups = summaries.filter((group) => matchesErrorFilter(group, source, severity));
  const discarded = discardedErrorCount(dropped, listedDropped);
  const wasTruncated = Boolean(truncated) || listedTruncated;

  return (
    <div className="space-y-4">
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
                {formatErrorSource(translate, item)}
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
      {discarded > 0 ? (
        <p className="text-sm text-warn" data-testid="monitor-dropped">
          {t("droppedDetails", { count: discarded })}
        </p>
      ) : null}
      {wasTruncated ? <p className="text-sm text-warn">{t("truncatedNotice")}</p> : null}
      {fetchError ? <p className="text-sm text-danger">{fetchError}</p> : null}
      {visibleGroups.length > 0 ? (
        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">{t("errorGroups")}</h4>
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
                {visibleGroups.map((group, index) => (
                  <tr
                    key={`${group.source}-${group.error_code}-${group.route}-${index}`}
                    className={cn("border-t border-line", group.severity === "critical" && "bg-danger-muted/30")}
                  >
                    <td className="px-3 py-2 align-top">
                      <button
                        type="button"
                        data-testid="monitor-error-group"
                        className="text-left text-fg hover:text-primary"
                        onClick={() => onFocus?.(group.last_ts ?? null)}
                      >
                        <span className="block font-medium">{formatErrorSource(translate, group.source)}</span>
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
        </section>
      ) : null}
      <section className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">{t("errorDetails")}</h4>
        {pending === "load" ? (
          <p className="rounded-lg border border-line bg-surface-raised/40 px-4 py-6 text-sm text-fg-muted">
            {t("errorsLoading")}
          </p>
        ) : items.length === 0 && visibleGroups.length === 0 ? (
          <p className="rounded-lg border border-line bg-surface-raised/40 px-4 py-6 text-sm text-fg-muted">
            {t("errorsEmpty")}
          </p>
        ) : items.length === 0 ? null : (
          <div className="overflow-x-auto rounded-lg border border-line" data-testid="monitor-error-details">
            <table className="w-full min-w-[48rem] text-left text-sm">
              <thead className="bg-surface-raised text-xs uppercase tracking-wide text-fg-subtle">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("errorTime")}</th>
                  <th className="px-3 py-2 font-medium">{t("errorSource")}</th>
                  <th className="px-3 py-2 font-medium">{t("errorSummary")}</th>
                  <th className="px-3 py-2 font-medium">{t("errorIds")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className={cn("border-t border-line", item.severity === "critical" && "bg-danger-muted/30")}>
                    <td className="px-3 py-2 align-top">
                      <button
                        type="button"
                        className="text-left text-fg hover:text-primary"
                        onClick={() => onFocus?.(item.ts)}
                      >
                        {formatMonitorInstant(item.ts, locale)}
                      </button>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <span className="block font-medium">{formatErrorSource(translate, item.source)}</span>
                      <span className="text-xs text-fg-subtle">{item.route ?? item.error_code}</span>
                    </td>
                    <td className="px-3 py-2 align-top text-fg-muted">
                      <p>{item.summary}</p>
                      {item.frames?.[0] ? (
                        <p className="mt-1 font-mono text-[11px] text-fg-subtle">
                          {item.frames[0].file}:{item.frames[0].line} {item.frames[0].function}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 align-top font-mono text-[11px] text-fg-subtle">
                      {item.request_id ? <div>req {item.request_id}</div> : null}
                      {item.operation_id ? <div>op {item.operation_id}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cursor ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="monitor-error-more"
            disabled={pending !== null}
            onClick={() => void loadMore()}
          >
            {t("loadMore")}
          </Button>
        ) : null}
      </section>
    </div>
  );
}
