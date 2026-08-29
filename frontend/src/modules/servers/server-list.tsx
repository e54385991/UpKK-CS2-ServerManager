import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { MapPin, Users, Radio, ServerOff, TriangleAlert } from "lucide-react";
import { listServers } from "@/modules/servers/api";
import { SERVER_STATUS_TONE } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";

/**
 * Async server component: fetches the server list on the server and renders it.
 * Wrapped in Suspense by the page so the App Shell paints instantly while this
 * streams in.
 */
export async function ServerList() {
  const t = await getTranslations("servers");
  const result = await listServers();

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

  return (
    <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {result.data.map((server) => {
        const tone = SERVER_STATUS_TONE[server.status];
        return (
          <li key={server.id}>
            <Link href={`/servers/${server.id}`} className="block h-full">
              <Card className="h-full p-5 transition-colors hover:border-line-strong hover:bg-surface-raised">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <p className="truncate text-sm font-semibold text-fg">
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
                    {t(`status.${server.status}`)}
                  </Badge>
                </div>

                {server.description ? (
                  <p className="mt-3 line-clamp-2 text-sm text-fg-muted">
                    {server.description}
                  </p>
                ) : null}

                <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-fg-muted">
                  <span className="inline-flex items-center gap-1.5">
                    <MapPin className="size-3.5 text-fg-subtle" />
                    {server.defaultMap}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Users className="size-3.5 text-fg-subtle" />
                    {t("players", { count: server.maxPlayers })}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Radio className="size-3.5 text-fg-subtle" />
                    {server.gamePort}
                  </span>
                </div>
              </Card>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function ServerListSkeleton() {
  return (
    <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <li
          key={index}
          className="h-40 animate-pulse rounded-lg border border-line bg-surface"
        />
      ))}
    </ul>
  );
}
