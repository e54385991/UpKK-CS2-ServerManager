import { Suspense } from "react";
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
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { getTranslations } from "next-intl/server";
import {
  listServers,
  getOverviewSummary,
  listOverviewHostSystemInfo,
} from "@/modules/servers/api";
import { SERVER_STATUS_TONE } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { cn } from "@/shared/lib/cn";

import { OverviewHostInfo, OverviewHostInfoSkeleton } from "./overview-host-info";

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

export async function OverviewStats() {
  // Start the slow host probe immediately, but let only its own Suspense
  // boundary wait for it. Counters and recent servers stream independently.
  const hostInfoResult = listOverviewHostSystemInfo();
  const [t, tServers, summaryResult, result] = await Promise.all([
    getTranslations("overview"),
    getTranslations("servers"),
    getOverviewSummary(),
    listServers(),
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
      <div data-testid="overview-stats" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
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
      <Suspense fallback={<OverviewHostInfoSkeleton />}>
        <OverviewHostInfo result={hostInfoResult} servers={servers} />
      </Suspense>
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
