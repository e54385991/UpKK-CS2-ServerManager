export function uniqueNonEmpty(values: Iterable<string | null | undefined>): string[] {
  const seen = new Set<string>();
  for (const value of values) {
    if (value) seen.add(value);
  }
  return [...seen];
}

export function matchesErrorFilter(
  item: { readonly source?: string | null; readonly severity?: string | null },
  source: string,
  severity: string,
): boolean {
  if (source !== "all" && item.source !== source) return false;
  if (severity !== "all" && item.severity !== severity) return false;
  return true;
}

export function focusedSeries<T extends { readonly ts: string }>(
  series: readonly T[],
  focusTs: string | null,
  windowMs = 15 * 60 * 1000,
): T[] {
  if (!focusTs) return [...series];
  const target = Date.parse(focusTs);
  if (Number.isNaN(target)) return [...series];
  return series.filter((point) => Math.abs(Date.parse(point.ts) - target) <= windowMs);
}

export function monitorStatusKey(
  view: {
    status: {
      enabled: boolean;
      stale: boolean;
      history_available: boolean;
    };
    series?: readonly unknown[];
  } | null,
  tone: "ok" | "warn" | "danger" | "neutral",
): "loading" | "disabled" | "stale" | "historyUnavailable" | "critical" | "watch" | "collecting" | "healthy" {
  if (view == null) return "loading";
  if (!view.status.enabled) return "disabled";
  if (view.status.stale) return "stale";
  if (!view.status.history_available) return "historyUnavailable";
  if (tone === "danger") return "critical";
  if (tone === "warn") return "watch";
  if ((view.series?.length ?? 0) === 0) return "collecting";
  return "healthy";
}

export function kpiTone(
  value: number | null | undefined,
  watch: number,
  critical: number,
): "ok" | "warn" | "danger" {
  if (value == null) return "ok";
  if (value >= critical) return "danger";
  if (value >= watch) return "warn";
  return "ok";
}

export function discardedErrorCount(statusDropped: number | undefined, listedDropped: number): number {
  return Math.max(statusDropped ?? 0, listedDropped);
}
