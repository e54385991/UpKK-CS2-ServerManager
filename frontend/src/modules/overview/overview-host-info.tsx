import { Cpu, MemoryStick } from "lucide-react";
import type { useTranslations } from "next-intl";
import { getFormatter, getTranslations } from "next-intl/server";
import type { HostSystemInfo, ServerSummary } from "@/modules/servers/types";
import type { ApiResult } from "@/shared/api/server-fetch";
import { Card } from "@/shared/ui/card";
import { Badge } from "@/shared/ui/badge";

type OverviewTranslator = ReturnType<typeof useTranslations<"overview">>;
type DateTimeFormatter = Awaited<ReturnType<typeof getFormatter>>["dateTime"];

function formatBytes(value: number | null, unavailable: string): string {
  if (value == null || !Number.isFinite(value)) return unavailable;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}

function formatHostTime(
  value: string | null,
  unavailable: string,
  formatDateTime: DateTimeFormatter,
): string {
  if (!value) return unavailable;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? unavailable
    : formatDateTime(date, { dateStyle: "medium", timeStyle: "medium" });
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
  formatDateTime,
}: {
  info: HostSystemInfo;
  server: ServerSummary | undefined;
  unavailable: string;
  t: OverviewTranslator;
  formatDateTime: DateTimeFormatter;
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
            time: formatHostTime(info.collectedAt, unavailable, formatDateTime),
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
  formatDateTime,
}: {
  infos: readonly HostSystemInfo[];
  servers: readonly ServerSummary[];
  t: OverviewTranslator;
  formatDateTime: DateTimeFormatter;
}) {
  const unavailable = t("hostInfoUnavailableValue");
  const serversById = new Map(servers.map((server) => [server.id, server]));
  return (
    <section data-testid="overview-host-info">
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
              server={serversById.get(info.serverId)}
              unavailable={unavailable}
              t={t}
              formatDateTime={formatDateTime}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export async function OverviewHostInfo({
  servers,
  result,
}: {
  servers: readonly ServerSummary[];
  result: Promise<ApiResult<readonly HostSystemInfo[]>>;
}) {
  const [hostInfo, t, format] = await Promise.all([
    result, getTranslations("overview"), getFormatter(),
  ]);
  if (!hostInfo.ok) return null;
  return (
    <HostSystemInfoSection
      infos={hostInfo.data}
      servers={servers}
      t={t}
      formatDateTime={format.dateTime}
    />
  );
}

export function OverviewHostInfoSkeleton() {
  return <div data-testid="overview-host-loading" aria-busy="true" className="h-72 animate-pulse rounded-lg border border-line bg-surface" />;
}
