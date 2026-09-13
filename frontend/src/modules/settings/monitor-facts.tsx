"use client";

import { Sparkline } from "@/modules/settings/monitor-charts";
import { formatBytes, formatMs } from "@/modules/settings/diagnostics-format";
import type { MonitorView } from "@/modules/settings/monitor-types";
import type { PanelMonitorViewDto } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { useTranslations } from "next-intl";

export function MonitorKpi({
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

export function MonitorDetailPanels({
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
