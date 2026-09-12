"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Download,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { getDiagnosticsAction } from "@/modules/settings/actions";
import { collectBrowserMetrics } from "@/modules/settings/browser-metrics";
import {
  buildPerformanceExport,
  formatBytes,
  formatMs,
  formatPercent,
  performanceExportFilename,
} from "@/modules/settings/diagnostics-format";
import type { PanelPerformanceSnapshotDto } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";

type Banner = { readonly tone: "ok" | "danger"; readonly text: string };

function downloadSnapshot(snapshot: PanelPerformanceSnapshotDto) {
  const exported = buildPerformanceExport(snapshot, collectBrowserMetrics());
  const blob = new Blob([`${JSON.stringify(exported, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = performanceExportFilename(snapshot.captured_at);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PerformanceDiagnosticsCard() {
  const t = useTranslations("settings");
  const [snapshot, setSnapshot] = useState<PanelPerformanceSnapshotDto | null>(null);
  const [pending, setPending] = useState<"load" | "export" | null>("load");
  const [banner, setBanner] = useState<Banner | null>(null);

  async function load() {
    setPending("load");
    setBanner(null);
    const result = await getDiagnosticsAction();
    setPending(null);
    if (!result.ok) {
      setBanner({
        tone: "danger",
        text: t("performance.fetchError", { status: result.status || "network" }),
      });
      return;
    }
    setSnapshot(result.data);
  }

  useEffect(() => {
    let cancelled = false;
    async function initialLoad() {
      setPending("load");
      const result = await getDiagnosticsAction();
      if (cancelled) return;
      setPending(null);
      if (!result.ok) {
        setBanner({
          tone: "danger",
          text: t("performance.fetchError", { status: result.status || "network" }),
        });
        return;
      }
      setSnapshot(result.data);
    }
    void initialLoad();
    return () => {
      cancelled = true;
    };
  }, [t]);

  function onExport() {
    if (snapshot == null) return;
    setPending("export");
    downloadSnapshot(snapshot);
    setPending(null);
    setBanner({ tone: "ok", text: t("performance.exportSuccess") });
  }

  return (
    <div
      data-testid="settings-performance-card"
      className="space-y-4 rounded-lg border border-line bg-surface p-5 shadow-panel"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-fg-muted">{t("performance.processLocal")}</p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={pending !== null}
            onClick={() => void load()}
          >
            {pending === "load" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
            {t("performance.refresh")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={pending !== null || snapshot == null}
            onClick={onExport}
          >
            {pending === "export" ? <LoaderCircle className="animate-spin" /> : <Download />}
            {pending === "export" ? t("performance.exporting") : t("performance.export")}
          </Button>
        </div>
      </div>

      {snapshot ? <PriorityMetrics snapshot={snapshot} /> : null}

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
    </div>
  );
}

function PriorityMetrics({ snapshot }: { snapshot: PanelPerformanceSnapshotDto }) {
  const t = useTranslations("settings");
  const routes = snapshot.requests.by_route ?? [];
  const limiters = snapshot.limiters ?? [];
  const redisTone = snapshot.redis.connected ? "ok" : "danger";
  const errorTone =
    snapshot.requests.error_rate > 0.05
      ? "danger"
      : snapshot.requests.error_rate > 0
        ? "warn"
        : "ok";
  return (
    <div className="space-y-4">
      <p className="text-xs text-fg-subtle">
        {t("performance.capturedAt", { time: snapshot.captured_at })}
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          title={t("performance.requests")}
          tone={errorTone}
          lines={[
            `${t("performance.p95")} ${formatMs(snapshot.requests.latency_ms.p95)}`,
            `${t("performance.errorRate")} ${formatPercent(snapshot.requests.error_rate)}`,
            `${t("performance.rpm")} ${snapshot.requests.requests_per_minute.toFixed(1)}`,
            t("performance.samples", {
              count: snapshot.requests.sample_count,
              seconds: snapshot.requests.window_seconds,
            }),
          ]}
        />
        <MetricTile
          title={t("performance.process")}
          lines={[
            `${t("performance.rss")} ${formatBytes(snapshot.process.rss_bytes)}`,
            `${t("performance.loopLag")} ${formatMs(snapshot.process.event_loop_lag_ms)}`,
            `${t("performance.tasks")} ${snapshot.process.asyncio_tasks ?? "—"}`,
          ]}
        />
        <MetricTile
          title={t("performance.database")}
          lines={[
            `${t("performance.checkedOut")} ${snapshot.database.checked_out ?? "—"} / ${snapshot.database.pool_size}`,
            `${t("performance.overflow")} ${snapshot.database.overflow ?? "—"}`,
          ]}
        />
        <MetricTile
          title={t("performance.redis")}
          tone={redisTone}
          lines={[
            snapshot.redis.connected
              ? t("performance.connected")
              : t("performance.disconnected"),
            `${t("performance.ping")} ${formatMs(snapshot.redis.ping_ms)}`,
          ]}
        />
        <MetricTile
          title={t("performance.sshPool")}
          lines={[
            `${t("performance.inUse")} ${snapshot.ssh_pool.in_use} / ${snapshot.ssh_pool.connections}`,
            `${t("performance.leases")} ${snapshot.ssh_pool.leases}`,
          ]}
        />
        <MetricTile
          title={t("performance.limiters")}
          lines={limiters.map(
            (limiter) =>
              `${limiter.name} ${limiter.borrowers ?? 0}/${limiter.global_limit}`,
          )}
        />
        <MetricTile
          title={t("performance.operations")}
          lines={[
            `${t("performance.running")} ${snapshot.operations.running}`,
            `${t("performance.queued")} ${snapshot.operations.queued}`,
          ]}
        />
        <MetricTile
          title={t("performance.runtime")}
          lines={[
            snapshot.runtime.version,
            snapshot.runtime.git_sha,
            `${t("performance.pid")} ${snapshot.runtime.worker_pid}`,
          ]}
        />
      </div>
      {routes.length > 0 ? (
        <SlowRoutes
          thresholdMs={snapshot.requests.slow_request_threshold_ms ?? 500}
          rows={routes}
        />
      ) : null}
    </div>
  );
}

function MetricTile({
  title,
  lines,
  tone = "neutral",
}: {
  title: string;
  lines: string[];
  tone?: "neutral" | "ok" | "warn" | "danger";
}) {
  return (
    <div className="rounded-md border border-line bg-surface-raised p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-fg">{title}</p>
        {tone !== "neutral" ? <StatusDot tone={tone} /> : null}
      </div>
      <ul className="space-y-1 text-xs tabular-nums text-fg-muted">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function SlowRoutes({
  thresholdMs,
  rows,
}: {
  thresholdMs: number;
  rows: NonNullable<PanelPerformanceSnapshotDto["requests"]["by_route"]>;
}) {
  const t = useTranslations("settings");
  return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="min-w-full text-left text-xs">
        <caption className="sr-only">{t("performance.slow", { ms: thresholdMs })}</caption>
        <thead className="bg-surface-raised text-fg-muted">
          <tr>
            <th className="px-3 py-2 font-medium">{t("performance.route")}</th>
            <th className="px-3 py-2 font-medium">{t("performance.count")}</th>
            <th className="px-3 py-2 font-medium">{t("performance.p95")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((row) => (
            <tr key={`${row.method} ${row.route}`} className="border-t border-line">
              <td className="px-3 py-2 font-mono text-fg">
                {row.method} {row.route}
              </td>
              <td className="px-3 py-2 tabular-nums text-fg-muted">{row.count}</td>
              <td className="px-3 py-2 tabular-nums text-fg-muted">
                {formatMs(row.latency_ms.p95)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
