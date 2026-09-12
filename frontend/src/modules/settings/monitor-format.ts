import type { MonitorRange } from "@/modules/settings/monitor-types";

export function monitorExportFilename(range: MonitorRange, now = new Date()): string {
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `cs2-panel-monitor-${range}-${stamp}.json`;
}

export function lastPoint<T>(series: readonly T[]): T | undefined {
  return series.length > 0 ? series[series.length - 1] : undefined;
}

export function formatCpu(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(value >= 10 ? 0 : 1)}%`;
}

export function formatRpm(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 10 ? `${Math.round(value)}` : value.toFixed(1);
}

export function sparkValues<T extends { readonly ts: string }>(
  series: readonly T[],
  read: (point: T) => number | null,
): Array<number | null> {
  return series.map((point) => {
    const value = read(point);
    return value == null || Number.isNaN(value) ? null : value;
  });
}

export function overallTone(view: {
  status: {
    enabled: boolean;
    stale: boolean;
    history_available: boolean;
    config_sync_error?: string | null;
  };
  alerts?: readonly { severity: string }[];
  series?: readonly unknown[];
}): "ok" | "warn" | "danger" | "neutral" {
  if (!view.status.enabled) return "neutral";
  const alerts = view.alerts ?? [];
  if (alerts.some((alert) => alert.severity === "critical")) return "danger";
  if (alerts.length > 0) return "warn";
  if (view.status.stale || !view.status.history_available || view.status.config_sync_error) {
    return "warn";
  }
  if ((view.series?.length ?? 0) === 0) return "neutral";
  return "ok";
}

export const LOCALIZED_ALERT_IDS = [
  "stale",
  "request_p95",
  "http_5xx",
  "loop_lag",
  "db_pool",
  "fd",
  "redis",
  "unhandled",
  "failed_tasks",
  "log_errors",
] as const;

export function isLocalizedAlert(id: string): boolean {
  return (LOCALIZED_ALERT_IDS as readonly string[]).includes(id);
}
