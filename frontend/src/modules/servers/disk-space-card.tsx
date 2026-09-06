import { getTranslations } from "next-intl/server";
import { HardDrive, TriangleAlert } from "lucide-react";
import { diskHealth, diskHealthTone, DISK_LOW_GB } from "@/modules/servers/disk-space-health";
import { ServerDiskRefreshButton } from "@/modules/servers/disk-space-refresh";
import type { DiskSpace } from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";

function formatGb(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)} GB`;
}

function formatPercent(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

/**
 * Game-directory capacity for the overview.
 *
 * The snapshot is whatever the hourly Redis cache already holds — rendering
 * this card never opens an SSH session, because `du` over the game tree is
 * expensive. Reading fresh numbers is an explicit click.
 */
export async function DiskSpaceCard({
  serverId,
  gameDirectory,
  disk,
}: {
  serverId: number;
  gameDirectory: string;
  disk: DiskSpace | null;
}) {
  const t = await getTranslations("serverDetail");
  const health = diskHealth(disk);

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <div>
          <div className="flex items-center gap-2">
            <HardDrive className="size-4 text-fg-subtle" />
            <CardTitle>{t("disk.title")}</CardTitle>
            {health !== "unknown" ? (
              <Badge tone={diskHealthTone(health)}>{t(`disk.state.${health}`)}</Badge>
            ) : null}
          </div>
          <CardDescription>{t("disk.help")}</CardDescription>
        </div>
        <ServerDiskRefreshButton serverId={serverId} />
      </CardHeader>
      <CardContent className="space-y-3">
        {disk?.cached ? (
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
            <Metric label={t("disk.directoryUsed")} value={formatGb(disk.usedGb)} />
            <Metric label={t("disk.available")} value={formatGb(disk.availableGb)} />
            <Metric label={t("disk.total")} value={formatGb(disk.totalGb)} />
            <Metric label={t("disk.usage")} value={formatPercent(disk.usedPercent)} />
          </dl>
        ) : (
          <p className="text-sm text-fg-muted">{t("disk.noSnapshot")}</p>
        )}
        <p className="font-mono text-xs text-fg-subtle">{gameDirectory}</p>
        {health === "low" || health === "critical" ? (
          <p
            className={
              health === "critical"
                ? "flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/30 px-3 py-2 text-sm text-danger"
                : "flex items-start gap-2 rounded-md border border-warn/30 bg-warn-muted/30 px-3 py-2 text-sm text-warn"
            }
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{t("disk.lowWarning", { threshold: DISK_LOW_GB })}</span>
          </p>
        ) : null}
        <p className="text-xs text-fg-subtle">{t("disk.cacheNote")}</p>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-fg-subtle">{label}</dt>
      <dd className="text-sm font-medium text-fg">{value}</dd>
    </div>
  );
}
