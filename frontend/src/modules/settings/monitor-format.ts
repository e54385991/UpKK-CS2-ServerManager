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

export function isLocalizedAlert(id: string): boolean {
  return (LOCALIZED_ALERT_IDS as readonly string[]).includes(id);
}

const ALERT_TITLE = {
  stale: "alerts.stale.title",
  request_p95: "alerts.request_p95.title",
  http_5xx: "alerts.http_5xx.title",
  loop_lag: "alerts.loop_lag.title",
  db_pool: "alerts.db_pool.title",
  fd: "alerts.fd.title",
  redis: "alerts.redis.title",
  unhandled: "alerts.unhandled.title",
  failed_tasks: "alerts.failed_tasks.title",
  log_errors: "alerts.log_errors.title",
} as const;

const ALERT_TITLE_CRITICAL = {
  stale: "alerts.stale.titleCritical",
  request_p95: "alerts.request_p95.titleCritical",
  http_5xx: "alerts.http_5xx.titleCritical",
  loop_lag: "alerts.loop_lag.titleCritical",
  db_pool: "alerts.db_pool.titleCritical",
  fd: "alerts.fd.titleCritical",
  redis: "alerts.redis.titleCritical",
  unhandled: "alerts.unhandled.titleCritical",
  failed_tasks: "alerts.failed_tasks.titleCritical",
  log_errors: "alerts.log_errors.titleCritical",
} as const;

const ALERT_DETAIL = {
  stale: "alerts.stale.detail",
  request_p95: "alerts.request_p95.detail",
  http_5xx: "alerts.http_5xx.detail",
  loop_lag: "alerts.loop_lag.detail",
  db_pool: "alerts.db_pool.detail",
  fd: "alerts.fd.detail",
  redis: "alerts.redis.detail",
  unhandled: "alerts.unhandled.detail",
  failed_tasks: "alerts.failed_tasks.detail",
  log_errors: "alerts.log_errors.detail",
} as const;

const ALERT_DETAIL_CRITICAL = {
  stale: "alerts.stale.detailCritical",
  request_p95: "alerts.request_p95.detailCritical",
  http_5xx: "alerts.http_5xx.detailCritical",
  loop_lag: "alerts.loop_lag.detailCritical",
  db_pool: "alerts.db_pool.detailCritical",
  fd: "alerts.fd.detailCritical",
  redis: "alerts.redis.detailCritical",
  unhandled: "alerts.unhandled.detailCritical",
  failed_tasks: "alerts.failed_tasks.detailCritical",
  log_errors: "alerts.log_errors.detailCritical",
} as const;

const ALERT_GUIDANCE = {
  stale: "alerts.stale.guidance",
  request_p95: "alerts.request_p95.guidance",
  http_5xx: "alerts.http_5xx.guidance",
  loop_lag: "alerts.loop_lag.guidance",
  db_pool: "alerts.db_pool.guidance",
  fd: "alerts.fd.guidance",
  redis: "alerts.redis.guidance",
  unhandled: "alerts.unhandled.guidance",
  failed_tasks: "alerts.failed_tasks.guidance",
  log_errors: "alerts.log_errors.guidance",
} as const;

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
  const keys = alert.severity === "critical" ? ALERT_TITLE_CRITICAL : ALERT_TITLE;
  return t(keys[alert.id as keyof typeof ALERT_TITLE]);
}

export function formatAlertDetail(t: MonitorTranslate, alert: AlertCopy): string {
  if (!isLocalizedAlert(alert.id)) return alert.detail;
  const keys = alert.severity === "critical" ? ALERT_DETAIL_CRITICAL : ALERT_DETAIL;
  return t(keys[alert.id as keyof typeof ALERT_DETAIL], alertDetailValues(alert));
}

export function formatAlertGuidance(t: MonitorTranslate, alert: AlertCopy): string {
  if (!isLocalizedAlert(alert.id)) return alert.guidance;
  return t(ALERT_GUIDANCE[alert.id as keyof typeof ALERT_GUIDANCE]);
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
