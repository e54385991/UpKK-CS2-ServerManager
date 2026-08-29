import Link from "next/link";
import {
  Server,
  Activity,
  CircleCheck,
  CircleAlert,
  ArrowRight,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { listServers, getOverviewSummary } from "@/modules/servers/api";
import { SERVER_STATUS_META } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { cn } from "@/shared/lib/cn";

function StatCard({
  label,
  value,
  icon: Icon,
  tone = "primary",
}: {
  label: string;
  value: number | string;
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
      </div>
    </Card>
  );
}

export async function OverviewStats() {
  // Counts come from the server-side aggregate; the recent list reuses the
  // summaries endpoint. Both are fetched in parallel.
  const [summaryResult, result] = await Promise.all([
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
        暂时无法获取运维总览数据（{status || "网络错误"}）。
      </Card>
    );
  }

  const servers = result.data;
  const { total, running, attention, capacity } = summaryResult.data;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="服务器总数" value={total} icon={Server} />
        <StatCard label="运行中" value={running} icon={CircleCheck} tone="ok" />
        <StatCard
          label="需要关注"
          value={attention}
          icon={CircleAlert}
          tone={attention > 0 ? "warn" : "primary"}
        />
        <StatCard label="总容量（人）" value={capacity} icon={Activity} />
      </div>

      <Card>
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold text-fg">最近的服务器</h2>
          <Link
            href="/servers"
            className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-strong"
          >
            查看全部
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
        {servers.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-fg-muted">
            还没有服务器。前往「服务器」页面添加第一台。
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {servers.slice(0, 5).map((server) => {
              const meta = SERVER_STATUS_META[server.status];
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
                    <Badge tone={meta.tone}>
                      <StatusDot
                        tone={meta.tone}
                        pulse={server.status === "running"}
                      />
                      {meta.label}
                    </Badge>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function OverviewStatsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div
            key={index}
            className="h-[5.25rem] animate-pulse rounded-lg border border-line bg-surface"
          />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-lg border border-line bg-surface" />
    </div>
  );
}
