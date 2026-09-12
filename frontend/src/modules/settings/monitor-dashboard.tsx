"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  Download,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { getMonitorAction, getMonitorErrorsAction, saveSettingsAction } from "@/modules/settings/actions";
import { collectBrowserMetrics } from "@/modules/settings/browser-metrics";
import { MonitorChart, Sparkline } from "@/modules/settings/monitor-charts";
import { MonitorErrorCenter } from "@/modules/settings/monitor-errors";
import { formatBytes, formatMs, formatPercent } from "@/modules/settings/diagnostics-format";
import {
  formatAlertDetail,
  formatAlertGuidance,
  formatAlertTitle,
  formatCpu,
  formatMonitorInstant,
  formatRpm,
  lastPoint,
  monitorExportFilename,
  overallTone,
  sparkValues,
  type MonitorTranslate,
} from "@/modules/settings/monitor-format";
import {
  MONITOR_RANGES,
  MONITOR_VIEWS,
  type MonitorRange,
  type MonitorView,
} from "@/modules/settings/monitor-types";
import type { PanelMonitorViewDto } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Switch } from "@/shared/ui/switch";

type Banner = { readonly tone: "ok" | "danger"; readonly text: string };
const EMPTY_SERIES: NonNullable<PanelMonitorViewDto["series"]> = [];
const EMPTY_ALERTS: NonNullable<PanelMonitorViewDto["alerts"]> = [];
const EMPTY_INSTANCES: NonNullable<PanelMonitorViewDto["instances"]> = [];

export function MonitorDashboard() {
  const t = useTranslations("settings.monitor");
  const locale = useLocale();
  const translate = t as unknown as MonitorTranslate;
  const [view, setView] = useState<PanelMonitorViewDto | null>(null);
  const [range, setRange] = useState<MonitorRange>("1h");
  const [instanceId, setInstanceId] = useState<string | undefined>();
  const [tab, setTab] = useState<MonitorView>("overview");
  const [paused, setPaused] = useState(false);
  const [pending, setPending] = useState<"load" | "export" | "toggle" | null>("load");
  const [banner, setBanner] = useState<Banner | null>(null);
  const [focusTs, setFocusTs] = useState<string | null>(null);
  const [errorEpoch, setErrorEpoch] = useState(0);
  const enabledRef = useRef(true);
  const inflightRef = useRef(false);
  const loadRef = useRef<(silent?: boolean) => Promise<void>>(async () => {});

  useEffect(() => {
    let cancelled = false;
    async function tick(silent = false) {
      if (silent && (inflightRef.current || document.hidden)) return;
      inflightRef.current = true;
      if (!silent) setPending("load");
      try {
        const result = await getMonitorAction(range, instanceId);
        if (cancelled) return;
        if (!result.ok) {
          setBanner({ tone: "danger", text: t("fetchError", { status: result.status || "network" }) });
          return;
        }
        enabledRef.current = result.data.status.enabled;
        setView(result.data);
        setBanner(null);
      } finally {
        inflightRef.current = false;
        if (!cancelled) setPending(null);
      }
    }
    loadRef.current = tick;
    const schedule = () => {
      if (document.hidden) return;
      void tick(true);
    };
    void tick(false);
    if (paused) {
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setInterval(() => {
      if (enabledRef.current) schedule();
    }, 10_000);
    document.addEventListener("visibilitychange", schedule);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [instanceId, paused, range, t]);

  async function load(silent = false) {
    await loadRef.current(silent);
    if (!silent) setErrorEpoch((value) => value + 1);
  }

  async function onToggle(next: boolean) {
    enabledRef.current = next;
    setPending("toggle");
    const result = await saveSettingsAction({ panelMonitoringEnabled: next });
    if (!result.ok) {
      enabledRef.current = view?.status.enabled !== false;
      setPending(null);
      setBanner({ tone: "danger", text: t("toggleError", { status: result.status || "network" }) });
      return;
    }
    inflightRef.current = false;
    await load();
  }

  async function onExport() {
    if (view == null) return;
    setPending("export");
    const listed = await getMonitorErrorsAction({ range, instanceId, limit: 100 });
    const blob = new Blob(
      [`${JSON.stringify({
        ...view,
        errors: listed.ok ? listed.data : { error: listed.status || "network" },
        browser: collectBrowserMetrics(),
      }, null, 2)}\n`],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = monitorExportFilename(range);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setPending(null);
    setBanner({ tone: "ok", text: t("exportSuccess") });
  }

  const latest = lastPoint(view?.series ?? EMPTY_SERIES);
  const tone = view ? overallTone(view) : "neutral";
  const series = view?.series ?? EMPTY_SERIES;
  const alerts = view?.alerts ?? EMPTY_ALERTS;
  const instances = view?.instances ?? EMPTY_INSTANCES;
  const focused = useMemo(() => {
    if (!focusTs) return series;
    const target = Date.parse(focusTs);
    return series.filter((point) => Math.abs(Date.parse(point.ts) - target) <= 15 * 60 * 1000);
  }, [focusTs, series]);

  return (
    <div data-testid="settings-performance-card" className="space-y-5">
      <header className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <StatusDot tone={tone} pulse={view?.status.collecting === true} />
              <p className="text-xl font-semibold tracking-tight text-fg" data-testid="monitor-status">
                {statusLabel(t, view, tone)}
              </p>
              {alerts.length > 0 ? (
                <Badge tone={tone === "danger" ? "danger" : "warn"}>
                  {t("alertCount", { count: alerts.length })}
                </Badge>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-fg-muted">
              {t("lastSample", { time: formatMonitorInstant(view?.status.last_sample_at, locale) })}
            </p>
            {view?.status.stopped_at && !view.status.enabled ? (
              <p className="mt-1 text-sm text-warn">
                {t("stoppedAt", { time: formatMonitorInstant(view.status.stopped_at, locale) })}
              </p>
            ) : null}
            {view?.integrity.gap ? <p className="mt-1 text-sm text-warn">{t("gap")}</p> : null}
            {view?.integrity.partial_latency ? <p className="mt-1 text-sm text-warn">{t("partialLatency")}</p> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5">
              <Switch
                id="panel-monitoring-enabled"
                checked={view?.status.enabled === true}
                disabled={pending === "toggle" || view == null}
                onCheckedChange={(next) => void onToggle(next)}
                label={t("toggle")}
              />
              <label htmlFor="panel-monitoring-enabled" className="text-sm text-fg">
                {t("toggle")}
              </label>
            </div>
            <Button type="button" variant="secondary" size="sm" onClick={() => setPaused((current) => !current)}>
              {paused ? t("resume") : t("pause")}
            </Button>
            <Button type="button" variant="secondary" size="sm" disabled={pending !== null} onClick={() => void load()}>
              {pending === "load" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
              {t("refresh")}
            </Button>
            <Button type="button" variant="secondary" size="sm" disabled={pending !== null || view == null} onClick={() => void onExport()}>
              {pending === "export" ? <LoaderCircle className="animate-spin" /> : <Download />}
              {t("export")}
            </Button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {MONITOR_RANGES.map((item) => (
            <Button
              key={item}
              type="button"
              size="sm"
              variant={range === item ? "primary" : "outline"}
              data-testid={`monitor-range-${item}`}
              onClick={() => {
                setRange(item);
                setFocusTs(null);
              }}
            >
              {t(`range.${item}`)}
            </Button>
          ))}
          {instances.length > 1 ? (
            <select
              className="h-8 rounded-md border border-line bg-surface px-2 text-sm"
              value={instanceId ?? view?.instance_id}
              onChange={(event) => {
                setInstanceId(event.target.value);
                setFocusTs(null);
              }}
            >
              {instances.map((item) => (
                <option key={item.instance_id} value={item.instance_id}>
                  {item.instance_id}{item.current ? t("currentInstance") : ""}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        <p className="mt-3 text-xs text-fg-subtle">{t("toggleHelp")}</p>
      </header>

      {focusTs ? (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn/30 bg-warn-muted/40 px-3 py-2 text-sm text-fg"
          data-testid="monitor-focus"
        >
          <span>{t("focusHint", { time: formatMonitorInstant(focusTs, locale) })}</span>
          <Button type="button" size="sm" variant="outline" data-testid="monitor-clear-focus" onClick={() => setFocusTs(null)}>
            {t("clearFocus")}
          </Button>
        </div>
      ) : null}

      {banner ? (
        <div
          className={cn(
            "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
          role="status"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{banner.text}</span>
        </div>
      ) : null}

      {alerts.length > 0 ? (
        <ol className="space-y-2" data-testid="monitor-alerts">
          {alerts.map((alert) => (
            <li
              key={alert.id}
              className={cn(
                "rounded-lg border px-4 py-3",
                alert.severity === "critical"
                  ? "border-danger/40 bg-danger-muted/40"
                  : "border-warn/40 bg-warn-muted/40",
              )}
            >
              <p className="font-medium text-fg">{formatAlertTitle(translate, alert)}</p>
              <p className="mt-1 text-sm text-fg-muted">{formatAlertDetail(translate, alert)}</p>
              <p className="mt-1 text-xs text-fg-subtle">{formatAlertGuidance(translate, alert)}</p>
            </li>
          ))}
        </ol>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi
          label={t("kpiP95")}
          value={formatMs(latest?.latency_p95_ms)}
          hint={t("p95Peak")}
          tone={kpiTone(latest?.latency_p95_ms, 500, 1000)}
          spark={sparkValues(series, (point) => point.latency_p95_ms ?? null)}
        />
        <Kpi
          label={t("kpiError")}
          value={formatPercent(latest?.error_rate)}
          hint={t("errorHint", { count: latest?.status_5xx ?? 0 })}
          tone={(latest?.status_5xx ?? 0) > 0 ? "danger" : "ok"}
          spark={sparkValues(series, (point) => point.error_rate)}
        />
        <Kpi
          label={t("kpiFailed")}
          value={String(latest?.failed_tasks ?? 0)}
          hint={t("failedHint")}
          tone={(latest?.failed_tasks ?? 0) > 0 ? "danger" : "ok"}
          spark={sparkValues(series, (point) => point.failed_tasks)}
        />
        <Kpi
          label={t("kpiCpu")}
          value={formatCpu(latest?.cpu_percent)}
          hint={t("cpuHint")}
          tone="ok"
          spark={sparkValues(series, (point) => point.cpu_percent ?? null)}
        />
        <Kpi
          label={t("kpiRss")}
          value={formatBytes(latest?.rss_bytes)}
          hint={t("rssHint", { peak: formatBytes(latest?.rss_peak_bytes) })}
          tone="ok"
          spark={sparkValues(series, (point) => point.rss_bytes ?? null)}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        {MONITOR_VIEWS.map((item) => (
          <Button
            key={item}
            type="button"
            size="sm"
            variant={tab === item ? "primary" : "outline"}
            data-testid={`monitor-view-${item}`}
            onClick={() => setTab(item)}
          >
            {t(`views.${item}`)}
          </Button>
        ))}
      </div>

      {tab === "overview" || tab === "requests" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <MonitorChart
            title={t("chartLatency")}
            unit="ms"
            testId="monitor-chart-latency"
            markerTs={focusTs}
            series={focused.map((point) => ({ ts: point.ts, value: point.latency_p95_ms ?? null }))}
            thresholds={[
              { value: 500, label: "500", tone: "warn" },
              { value: 1000, label: "1000", tone: "danger" },
            ]}
            hint={t("p95Peak")}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartTraffic")}
            unit={t("unitRpm")}
            color="var(--color-info)"
            series={focused.map((point) => ({ ts: point.ts, value: point.requests_per_minute }))}
            hint={t("kpiRpm", { value: formatRpm(latest?.requests_per_minute) })}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartErrors")}
            unit={t("unitCount")}
            color="var(--color-danger)"
            series={focused.map((point) => ({ ts: point.ts, value: point.status_5xx }))}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartUnhandled")}
            unit={t("unitCount")}
            color="var(--color-warn)"
            series={focused.map((point) => ({ ts: point.ts, value: point.unhandled_exceptions }))}
          />
        </div>
      ) : null}

      {tab === "overview" || tab === "resources" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <MonitorChart
            markerTs={focusTs}
            title={t("chartCpu")}
            unit="%"
            series={focused.map((point) => ({ ts: point.ts, value: point.cpu_percent ?? null }))}
            hint={t("cpuHint")}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartRss")}
            unit="bytes"
            series={focused.map((point) => ({ ts: point.ts, value: point.rss_bytes ?? null }))}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartLoop")}
            unit="ms"
            thresholds={[
              { value: 50, label: "50", tone: "warn" },
              { value: 200, label: "200", tone: "danger" },
            ]}
            series={focused.map((point) => ({ ts: point.ts, value: point.loop_lag_p95_ms ?? null }))}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartDb")}
            unit={t("unitCount")}
            series={focused.map((point) => ({ ts: point.ts, value: point.db_checked_out ?? null }))}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartRedis")}
            unit="ms"
            color="var(--color-info)"
            series={focused.map((point) => ({ ts: point.ts, value: point.redis_ping_ms ?? null }))}
          />
        </div>
      ) : null}

      {tab === "overview" || tab === "tasks" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <MonitorChart
            markerTs={focusTs}
            title={t("chartQueue")}
            unit={t("unitCount")}
            series={focused.map((point) => ({ ts: point.ts, value: point.queue_queued }))}
          />
          <MonitorChart
            markerTs={focusTs}
            title={t("chartFailedTasks")}
            unit={t("unitCount")}
            color="var(--color-danger)"
            series={focused.map((point) => ({ ts: point.ts, value: point.failed_tasks }))}
          />
        </div>
      ) : null}

      {view?.snapshot ? <DetailPanels snapshot={view.snapshot} tab={tab} /> : null}

      <section>
        <h3 className="mb-2 text-sm font-semibold text-fg">{t("errors")}</h3>
        <MonitorErrorCenter
          range={range}
          instanceId={instanceId}
          groups={view?.error_groups}
          dropped={view?.status.dropped_error_details}
          truncated={view?.status.truncated_summaries}
          refreshEpoch={errorEpoch}
          onFocus={setFocusTs}
        />
      </section>
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  tone,
  spark,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "ok" | "warn" | "danger";
  spark: Array<number | null>;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4 shadow-panel">
      <p className="text-xs uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className={cn("mt-1 text-3xl font-semibold tabular-nums", tone === "danger" && "text-danger", tone === "warn" && "text-warn")}>
        {value}
      </p>
      <p className="mt-1 text-xs text-fg-muted">{hint}</p>
      <Sparkline values={spark} tone={tone} className="mt-3" />
    </div>
  );
}

function DetailPanels({
  snapshot,
  tab,
}: {
  snapshot: NonNullable<PanelMonitorViewDto["snapshot"]>;
  tab: MonitorView;
}) {
  const t = useTranslations("settings.monitor");
  const routes = snapshot.requests.by_route ?? [];
  const maxP95 = Math.max(...routes.map((row) => row.latency_ms.p95), 1);
  if (tab === "requests") {
    return (
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Fact label={t("inFlight")} value={String(snapshot.requests.in_flight ?? 0)} />
          <Fact label={t("statusMix")} value={`${snapshot.requests.status_2xx} / ${snapshot.requests.status_4xx} / ${snapshot.requests.status_5xx}`} />
          <Fact label={t("unhandledCount")} value={String(snapshot.requests.unhandled_exceptions ?? 0)} />
          <Fact label={t("cancellations")} value={String(snapshot.requests.cancellations ?? 0)} />
        </div>
        <section className="rounded-lg border border-line bg-surface p-5">
          <h3 className="text-sm font-semibold text-fg">{t("slowRoutes")}</h3>
          <ul className="mt-3 space-y-2">
            {routes.map((row) => (
              <li key={`${row.method}${row.route}`} className="text-sm">
                <div className="flex justify-between gap-3">
                  <span className="truncate font-mono text-xs">{row.method} {row.route}</span>
                  <span className="tabular-nums text-fg-muted">{formatMs(row.latency_ms.p95)}</span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-surface-overlay">
                  <div className="h-full rounded-full bg-primary" style={{ width: `${(row.latency_ms.p95 / maxP95) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    );
  }
  if (tab === "resources") {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Fact label={t("redis")} value={snapshot.redis.connected ? t("connected") : t("disconnected")} />
        <Fact label={t("dbPool")} value={`${snapshot.database.checked_out ?? "—"} / ${snapshot.database.capacity ?? snapshot.database.pool_size + snapshot.database.max_overflow}`} />
        <Fact label={t("ssh")} value={`${snapshot.ssh_pool.in_use}/${snapshot.ssh_pool.connections}`} />
        <Fact label={t("outbound")} value={String((snapshot.outbound_http ?? []).reduce((sum, item) => sum + item.calls, 0))} />
        <Fact label={t("fd")} value={`${snapshot.process.fd_open ?? "—"} / ${snapshot.process.fd_limit ?? "—"}`} />
        <Fact label={t("borrowers")} value={String((snapshot.limiters?.[0]?.borrowers ?? 0))} />
      </div>
    );
  }
  if (tab === "tasks") {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Fact label={t("queued")} value={String(snapshot.operations.queued)} />
        <Fact label={t("running")} value={String(snapshot.operations.running)} />
        <Fact label={t("failed")} value={String(snapshot.operations.failed ?? 0)} />
        <Fact label={t("oldest")} value={formatMs(snapshot.operations.oldest_queue_ms)} />
      </div>
    );
  }
  return null;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <p className="text-xs text-fg-subtle">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function statusLabel(
  t: ReturnType<typeof useTranslations>,
  view: PanelMonitorViewDto | null,
  tone: "ok" | "warn" | "danger" | "neutral",
): string {
  if (view == null) return t("loading");
  if (!view.status.enabled) return t("disabled");
  if (view.status.stale) return t("stale");
  if (!view.status.history_available) return t("historyUnavailable");
  if (tone === "danger") return t("critical");
  if (tone === "warn") return t("watch");
  if ((view.series?.length ?? 0) === 0) return t("collecting");
  return t("healthy");
}

function kpiTone(value: number | null | undefined, watch: number, critical: number): "ok" | "warn" | "danger" {
  if (value == null) return "ok";
  if (value >= critical) return "danger";
  if (value >= watch) return "warn";
  return "ok";
}
