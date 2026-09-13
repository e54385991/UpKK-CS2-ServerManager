import type { MonitorRange } from "@/modules/settings/monitor-types";

export type MonitorTranslate = (
  key: string,
  values?: Record<string, string | number>,
) => string;

type AlertCopy = {
  readonly id: string;
  readonly severity: string;
  readonly title: string;
  readonly detail: string;
  readonly guidance: string;
  readonly value?: number | null;
};

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

export type LocalizedAlertId = (typeof LOCALIZED_ALERT_IDS)[number];

export function isLocalizedAlert(id: string): id is LocalizedAlertId {
  return (LOCALIZED_ALERT_IDS as readonly string[]).includes(id);
}

const SOURCE_KEYS = {
  request: "sources.request",
  database: "sources.database",
  redis: "sources.redis",
  ssh: "sources.ssh",
  http: "sources.http",
  task: "sources.task",
  log: "sources.log",
} as const;

export function formatAlertTitle(t: MonitorTranslate, alert: AlertCopy): string {
  if (!isLocalizedAlert(alert.id)) return alert.title;
  return t(alertCopyKey(alert.id, "title", alert.severity === "critical"));
}

export function formatAlertDetail(t: MonitorTranslate, alert: AlertCopy): string {
  if (!isLocalizedAlert(alert.id)) return alert.detail;
  return t(alertCopyKey(alert.id, "detail", alert.severity === "critical"), alertDetailValues(alert));
}

export function formatAlertGuidance(t: MonitorTranslate, alert: AlertCopy): string {
  if (!isLocalizedAlert(alert.id)) return alert.guidance;
  return t(alertCopyKey(alert.id, "guidance", false));
}

export function formatErrorSource(t: MonitorTranslate, source: string | null | undefined): string {
  if (!source) return "—";
  if (!(source in SOURCE_KEYS)) return source;
  return t(SOURCE_KEYS[source as keyof typeof SOURCE_KEYS]);
}

export function formatMonitorInstant(
  iso: string | null | undefined,
  locale: string,
  now = Date.now(),
): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(
    ...relativeParts(now - date.getTime()),
  );
  const clock = date.toLocaleString(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return `${relative} (${clock})`;
}

function alertCopyKey(id: LocalizedAlertId, field: "title" | "detail" | "guidance", critical: boolean): string {
  if (field === "guidance" || !critical) return `alerts.${id}.${field}`;
  return `alerts.${id}.${field}Critical`;
}

function relativeParts(elapsedMs: number): [number, Intl.RelativeTimeFormatUnit] {
  const elapsedSec = Math.round(elapsedMs / 1000);
  const abs = Math.abs(elapsedSec);
  const sign = elapsedSec >= 0 ? -1 : 1;
  if (abs < 60) return [sign * abs, "second"];
  if (abs < 3600) return [sign * Math.round(abs / 60), "minute"];
  if (abs < 86400) return [sign * Math.round(abs / 3600), "hour"];
  return [sign * Math.round(abs / 86400), "day"];
}

function ratioPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(value >= 0.1 ? 1 : 2)}%`;
}

function alertDetailValues(alert: AlertCopy): Record<string, string | number> {
  const value = alert.value;
  return {
    ms: Math.round(value ?? 0),
    count: Math.round(value ?? 0),
    percent: ratioPercent(value),
    samples: 3,
    minRequests: 20,
  };
}
