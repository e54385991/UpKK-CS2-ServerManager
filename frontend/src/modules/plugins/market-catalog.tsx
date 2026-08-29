import Link from "next/link";
import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import { Package, Puzzle, TriangleAlert } from "lucide-react";
import { listMarketPlugins } from "@/modules/plugins/api";
import {
  PLUGIN_CATEGORIES,
  type MarketQuery,
} from "@/modules/plugins/types";
import { Badge } from "@/shared/ui/badge";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

function hrefFor(pluginId: number, serverId?: number): Route {
  const query = serverId ? `?serverId=${serverId}` : "";
  return `/plugins/${pluginId}${query}` as Route;
}

function pageHref(query: MarketQuery, offset: number, serverId?: number): Route {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.category) params.set("category", query.category);
  if (serverId) params.set("serverId", String(serverId));
  if (offset > 0) params.set("offset", String(offset));
  const qs = params.toString();
  return (qs ? `/plugins?${qs}` : "/plugins") as Route;
}

export async function MarketCatalog({
  query,
  serverId,
}: {
  query: MarketQuery;
  serverId?: number;
}) {
  const t = await getTranslations("plugins");
  const result = await listMarketPlugins(query);

  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }

  const { items, total, limit, offset } = result.data;
  if (total === 0) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-surface-overlay text-fg-subtle">
          <Package className="size-6" />
        </span>
        <p className="text-sm font-medium text-fg">{t("emptyTitle")}</p>
        <p className="text-sm text-fg-muted">{t("emptyDesc")}</p>
      </Card>
    );
  }

  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <div className="space-y-4">
      <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {items.map((plugin) => (
          <li key={plugin.id}>
            <Link href={hrefFor(plugin.id, serverId)} className="block h-full">
              <Card className="h-full p-5 transition-colors hover:border-line-strong hover:bg-surface-raised">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <p className="truncate text-sm font-semibold text-fg">
                      {plugin.title}
                    </p>
                    <p className="truncate text-xs text-fg-subtle">
                      {plugin.author || t("unknownAuthor")}
                      {plugin.version ? ` · ${plugin.version}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                    {plugin.isRecommended ? (
                      <Badge tone="primary">{t("recommended")}</Badge>
                    ) : null}
                    <Badge tone="neutral">
                      {(PLUGIN_CATEGORIES as readonly string[]).includes(
                        plugin.category,
                      )
                        ? t(`categories.${plugin.category}`)
                        : plugin.category}
                    </Badge>
                  </div>
                </div>
                {plugin.description ? (
                  <p className="mt-3 line-clamp-3 text-sm text-fg-muted">
                    {plugin.description}
                  </p>
                ) : null}
                <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle">
                  <span className="inline-flex items-center gap-1.5">
                    <Puzzle className="size-3.5" />
                    {t("installs", { count: plugin.installCount })}
                  </span>
                  {plugin.dependencies.length > 0 ? (
                    <span>
                      {t("dependencyCount", {
                        count: plugin.dependencies.length,
                      })}
                    </span>
                  ) : null}
                </div>
              </Card>
            </Link>
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between text-sm text-fg-muted">
        <p>{t("pageRange", { from: offset + 1, to: Math.min(offset + limit, total), total })}</p>
        <div className="flex gap-3">
          {hasPrev ? (
            <Link
              href={pageHref(query, Math.max(0, offset - limit), serverId)}
              className="text-primary hover:underline"
            >
              {t("previous")}
            </Link>
          ) : (
            <span className="text-fg-subtle">{t("previous")}</span>
          )}
          {hasNext ? (
            <Link
              href={pageHref(query, offset + limit, serverId)}
              className="text-primary hover:underline"
            >
              {t("next")}
            </Link>
          ) : (
            <span className="text-fg-subtle">{t("next")}</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function MarketCatalogSkeleton() {
  return (
    <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {Array.from({ length: 4 }).map((_, index) => (
        <li key={index} className="rounded-lg border border-line bg-surface p-5">
          <Skeleton className="mb-2 h-4 w-40" />
          <Skeleton className="mb-4 h-3 w-24" />
          <Skeleton className="h-12 w-full" />
        </li>
      ))}
    </ul>
  );
}