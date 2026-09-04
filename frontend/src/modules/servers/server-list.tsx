import Link from "next/link";
import type { Route } from "next";
import { getFormatter, getTranslations } from "next-intl/server";
import { ServerOff, TriangleAlert } from "lucide-react";
import {
  getSteamLatestVersion,
  listA2SCache,
  listDiskSpace,
  listServers,
} from "@/modules/servers/api";
import { ServerFleet } from "@/modules/servers/server-fleet";
import type {
  A2SCache,
  DiskSpace,
  ServerListScope,
  ServerStatus,
  SteamLatestVersion,
} from "@/modules/servers/types";
import { SERVER_STATUS_GROUPS, serversHref } from "@/modules/servers/workspace";
import { Card } from "@/shared/ui/card";
import { cn } from "@/shared/lib/cn";

export async function ServerList({
  status,
  scope = "mine",
  isAdmin = false,
}: {
  status?: ServerStatus;
  scope?: ServerListScope;
  isAdmin?: boolean;
}) {
  const [t, format, result, steam, disk, a2s] = await Promise.all([
    getTranslations("servers"),
    getFormatter(),
    listServers(scope),
    getSteamLatestVersion(),
    listDiskSpace(scope),
    listA2SCache(scope),
  ]);

  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }

  if (result.data.length === 0) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-surface-overlay text-fg-subtle">
          <ServerOff className="size-6" />
        </span>
        <div className="space-y-1">
          <p className="text-sm font-medium text-fg">{t("emptyTitle")}</p>
          <p className="text-sm text-fg-muted">{t("emptyDesc")}</p>
        </div>
        <Link
          href="/servers/new"
          className="mt-2 inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary-strong"
        >
          {t("add")}
        </Link>
      </Card>
    );
  }

  const counts = Object.fromEntries(
    SERVER_STATUS_GROUPS.map((key) => [
      key,
      result.data.filter((server) => server.status === key).length,
    ]),
  ) as Record<ServerStatus, number>;

  const visible = status
    ? result.data.filter((server) => server.status === status)
    : result.data;

  const diskById = Object.fromEntries(
    (disk.ok ? disk.data : []).map((item) => [item.serverId, item]),
  ) as Record<number, DiskSpace>;
  const a2sById = Object.fromEntries(
    (a2s.ok ? a2s.data : []).map((item) => [item.serverId, item]),
  ) as Record<number, A2SCache>;

  return (
    <div className="space-y-6">
      {steam.ok && steam.data.available ? (
        <SteamVersionBanner
          steam={steam.data}
          label={t("steamLatestVersion")}
          updated={t("updated")}
          formatDateTime={format.dateTime}
        />
      ) : null}
      {isAdmin ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-fg-subtle">
            {t("fleetLabel")}
          </span>
          <CategoryChip
            href={serversHref({ status })}
            label={t("fleetMine")}
            count={scope === "mine" ? result.data.length : undefined}
            active={scope === "mine"}
          />
          <CategoryChip
            href={serversHref({ status, scope: "all" })}
            label={t("fleetAll")}
            count={scope === "all" ? result.data.length : undefined}
            active={scope === "all"}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <CategoryChip
          href={serversHref({ scope })}
          label={t("allCategories")}
          count={result.data.length}
          active={status == null}
        />
        {SERVER_STATUS_GROUPS.map((key) => (
          <CategoryChip
            key={key}
            href={serversHref({ status: key, scope })}
            label={t(`status.${key}`)}
            count={counts[key]}
            active={status === key}
          />
        ))}
      </div>

      {visible.length === 0 ? (
        <Card className="px-5 py-10 text-center text-sm text-fg-muted">
          {t("emptyCategory")}
        </Card>
      ) : (
        <ServerFleet
          servers={visible}
          diskById={diskById}
          a2sById={a2sById}
          steam={steam.ok ? steam.data : null}
          scope={scope}
          showOwner={scope === "all"}
        />
      )}
    </div>
  );
}

function CategoryChip({
  href,
  label,
  count,
  active,
}: {
  href: Route;
  label: string;
  count?: number;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-primary/40 bg-primary-muted text-primary"
          : "border-line bg-surface text-fg-muted hover:border-line-strong hover:text-fg",
      )}
    >
      {label}
      {count != null ? (
        <span className={active ? "text-primary" : "text-fg-subtle"}>
          {count}
        </span>
      ) : null}
    </Link>
  );
}

type DateTimeFormatter = Awaited<ReturnType<typeof getFormatter>>["dateTime"];

function formatTimestamp(
  value: string | null,
  formatDateTime: DateTimeFormatter,
): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return formatDateTime(date, { dateStyle: "medium", timeStyle: "medium" });
}

function SteamVersionBanner({
  steam,
  label,
  updated,
  formatDateTime,
}: {
  steam: SteamLatestVersion;
  label: string;
  updated: string;
  formatDateTime: DateTimeFormatter;
}) {
  return (
    <Card className="border-primary/20 bg-primary-muted/40 px-5 py-3 text-sm text-fg">
      <p>
        <span className="font-medium">{label}</span>{" "}
        <span className="font-mono">{steam.version}</span>
        {steam.timestamp ? (
          <span className="ms-2 text-xs text-fg-muted">
            ({updated} {formatTimestamp(steam.timestamp, formatDateTime)})
          </span>
        ) : null}
      </p>
    </Card>
  );
}

export function ServerListSkeleton() {
  return (
    <div className="space-y-6">
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <li
            key={index}
            className="h-40 animate-pulse rounded-lg border border-line bg-surface"
          />
        ))}
      </ul>
    </div>
  );
}
