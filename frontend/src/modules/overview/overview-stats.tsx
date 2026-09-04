import Link from "next/link";
import type { Route } from "next";
import {
  Server,
  Activity,
  CircleCheck,
  CircleAlert,
  ArrowRight,
  Cable,
  BookOpen,
  Cpu,
  MemoryStick,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { getTranslations } from "next-intl/server";
import {
  listServers,
  getOverviewSummary,
  listOverviewHostSystemInfo,
} from "@/modules/servers/api";
import type { HostSystemInfo, ServerSummary } from "@/modules/servers/types";
import { SERVER_STATUS_TONE } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { cn } from "@/shared/lib/cn";

function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "primary",
}: {
  label: string;
  value: number | string;
  hint?: string;
  icon: LucideIcon;
  tone?: "primary" | "ok" | "warn" | "danger";
}) {
  const toneClass = {
    primary: "text-primary bg-primary-muted ring-primary/30",
    ok: "text-ok bg-ok-muted ring-ok/30",
    warn: "text-warn bg-warn-muted ring-warn/30",
    danger: "text-danger bg-danger-muted ring-danger/30",
  }[tone];
  return (
    <Card className="flex items-center gap-4 p-5">
      <span
        className={cn(
          "flex size-11 items-center justify-center rounded-lg ring-1",
          toneClass,
        )}
      >
        <Icon className="size-5" />
      </span>
      <div>
        <p className="text-2xl font-semibold tabular-nums tracking-tight text-fg">
          {value}
        </p>
        <p className="text-xs text-fg-muted">{label}</p>
        {hint ? <p className="text-[11px] text-fg-subtle">{hint}</p> : null}
      </div>
    </Card>
  );
}

function formatBytes(value: number | null, unavailable: string): string {
  if (value == null || !Number.isFinite(value)) return unavailable;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}

function formatHostTime(value: string | null, unavailable: string): string {
  if (!value) return unavailable;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? unavailable : date.toLocaleString();
}

function HostInfoField({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-fg-subtle">{label}</dt>
      <dd className="mt-1 break-words text-sm text-fg">{value}</dd>
    </div>
  );
}

function HostSystemInfoCard({
  info,
  server,
  unavailable,
  t,
}: {
  info: HostSystemInfo;
  server: ServerSummary | undefined;
  unavailable: string;
  t: (key: string, values?: Record<string, string | number>) => string;
}) {
  const distribution = info.distributionPrettyName
    ? info.distributionPrettyName
    : [info.distribution, info.distributionVersion].filter(Boolean).join(" ") || unavailable;
  const cpuCores = info.cpuCores == null
    ? unavailable
    : t("hostInfoCores", { count: info.cpuCores });
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg">
            {server?.name ?? t("hostInfoUnknownServer")}
          </p>
          <p className="mt-0.5 truncate font-mono text-xs text-fg-subtle">
            {server?.host ?? unavailable}
          </p>
        </div>
        <Badge tone={info.success ? "ok" : "warn"}>
          {t(info.success ? "hostInfoReady" : "hostInfoUnavailable")}
        </Badge>
      </div>
      <dl className="mt-5 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
        <HostInfoField
          label={t("hostInfoSystem")}
          value={info.systemType ?? unavailable}
        />
        <HostInfoField
          label={t("hostInfoDistribution")}
          value={distribution}
        />
        <HostInfoField
          label={t("hostInfoKernel")}
          value={info.kernelVersion ?? unavailable}
        />
        <HostInfoField
          label={t("hostInfoArchitecture")}
          value={info.architecture ?? unavailable}
        />
        <HostInfoField
          label={t("hostInfoCpu")}
          value={info.cpuModel ?? unavailable}
        />
        <HostInfoField label={t("hostInfoCpuCores")} value={cpuCores} />
        <HostInfoField
          label={t("hostInfoMemoryTotal")}
          value={formatBytes(info.memoryTotalBytes, unavailable)}
        />
        <HostInfoField
          label={t("hostInfoMemoryAvailable")}
          value={formatBytes(info.memoryAvailableBytes, unavailable)}
        />
      </dl>
      <div className="mt-5 flex items-center gap-2 border-t border-line pt-3 text-[11px] text-fg-subtle">
        <MemoryStick className="size-3.5" />
        <span>
          {t("hostInfoUpdated", {
            time: formatHostTime(info.collectedAt, unavailable),
          })}
        </span>
      </div>
    </Card>
  );
}

function HostSystemInfoSection({
  infos,
  servers,
  t,
}: {
  infos: readonly HostSystemInfo[];
  servers: readonly ServerSummary[];
  t: (key: string, values?: Record<string, string | number>) => string;
}) {
  const unavailable = t("hostInfoUnavailableValue");
  return (
    <section>
      <div className="mb-3 flex items-start gap-3">
        <span className="flex size-9 items-center justify-center rounded-lg bg-primary-muted text-primary ring-1 ring-primary/30">
          <Cpu className="size-4" />
        </span>
        <div>
          <h2 className="text-sm font-semibold text-fg">{t("hostInfoTitle")}</h2>
          <p className="mt-0.5 text-xs text-fg-muted">{t("hostInfoHint")}</p>
        </div>
      </div>
      {infos.length === 0 ? (
        <Card className="px-5 py-8 text-center text-sm text-fg-muted">
          {t("hostInfoEmpty")}
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {infos.map((info) => (
            <HostSystemInfoCard
              key={info.serverId}
              info={info}
              server={servers.find((item) => item.id === info.serverId)}
              unavailable={unavailable}
              t={t}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export async function OverviewStats() {
  // Counts come from the server-side aggregate; the recent list reuses the
  // summaries endpoint. Both are fetched in parallel.
  const [t, tServers, summaryResult, result, hostInfoResult] = await Promise.all([
    getTranslations("overview"),
    getTranslations("servers"),
    getOverviewSummary(),
    listServers(),
    listOverviewHostSystemInfo(),
  ]);

  if (!summaryResult.ok || !result.ok) {
    const status = !summaryResult.ok
      ? summaryResult.status
      : !result.ok
        ? result.status
        : 0;
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: status || "network" })}
      </Card>
    );
  }

  const servers = result.data;
  const {
    total,
    running,
    attention,
    capacity,
    sshConnections,
    sshInUse,
    sshIdle,
    sshLeases,
  } = summaryResult.data;

  return (
    <div className="space-y-6">
      <Card className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg bg-primary-muted text-primary ring-1 ring-primary/30">
            <BookOpen className="size-5" />
          </span>
          <div>
            <p className="text-sm font-semibold text-fg">{t("tutorialLink")}</p>
            <p className="mt-0.5 text-xs text-fg-muted">{t("tutorialHelp")}</p>
          </div>
        </div>
        <Link
          href={"/deployment-tutorial" as Route}
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:text-primary-strong"
        >
          {t("tutorialOpen")}
          <ArrowRight className="size-3.5" />
        </Link>
      </Card>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label={t("total")} value={total} icon={Server} />
        <StatCard
          label={t("running")}
          value={running}
          icon={CircleCheck}
          tone="ok"
        />
        <StatCard
          label={t("attention")}
          value={attention}
          icon={CircleAlert}
          tone={attention > 0 ? "warn" : "primary"}
        />
        <StatCard label={t("capacity")} value={capacity} icon={Activity} />
        <StatCard
          label={t("sshConnections")}
          value={sshConnections}
          hint={t("sshConnectionsHint", {
            leases: sshLeases,
            idle: sshIdle,
          })}
          icon={Cable}
          tone={sshLeases > 0 || sshInUse > 0 ? "ok" : "primary"}
        />
      </div>

      <Card>
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold text-fg">{t("recent")}</h2>
          <Link
            href="/servers"
            className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-strong"
          >
            {t("viewAll")}
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
        {servers.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-fg-muted">
            {t("emptyRecent")}
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {servers.slice(0, 5).map((server) => {
              const tone = SERVER_STATUS_TONE[server.status];
              return (
                <li key={server.id}>
                  <Link
                    href={`/servers/${server.id}`}
                    className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-overlay/50"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-fg">
                        {server.name}
                      </p>
                      <p className="truncate font-mono text-xs text-fg-subtle">
                        {server.host}:{server.gamePort}
                      </p>
                    </div>
                    <Badge tone={tone}>
                      <StatusDot
                        tone={tone}
                        pulse={server.status === "running"}
                      />
                      {tServers(`status.${server.status}`)}
                    </Badge>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      {hostInfoResult.ok ? (
        <HostSystemInfoSection
          infos={hostInfoResult.data}
          servers={servers}
          t={t}
        />
      ) : null}
    </div>
  );
}

export function OverviewStatsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="h-[5.25rem] animate-pulse rounded-lg border border-line bg-surface"
          />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-lg border border-line bg-surface" />
      <div className="h-72 animate-pulse rounded-lg border border-line bg-surface" />
    </div>
  );
}
