import Link from "next/link";
import type { Route } from "next";
import { getLocale, getTranslations } from "next-intl/server";
import { Package, TriangleAlert } from "lucide-react";
import type { listMarketPlugins } from "@/modules/plugins/api";
import { MarketCatalogItems } from "@/modules/plugins/market-catalog-items";
import {
  localizedPluginDescription,
  type MarketInstallServer,
  type MarketQuery,
} from "@/modules/plugins/types";
import { DEFAULT_LOCALE, isLocale } from "@/i18n/config";
import { markdownToPlainText } from "@/shared/lib/markdown";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

function pageHref(query: MarketQuery, offset: number, serverId?: number): Route {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.category) params.set("category", query.category);
  if (query.framework) params.set("framework", query.framework);
  if (query.sort && query.sort !== "recommended") params.set("sort", query.sort);
  if (serverId) params.set("serverId", String(serverId));
  if (offset > 0) params.set("offset", String(offset));
  const qs = params.toString();
  return (qs ? `/plugins?${qs}` : "/plugins") as Route;
}

export async function MarketCatalog({
  query,
  resultPromise,
  serverId,
  servers,
  canDelete = false,
}: {
  query: MarketQuery;
  resultPromise: ReturnType<typeof listMarketPlugins>;
  serverId?: number;
  servers: readonly MarketInstallServer[];
  canDelete?: boolean;
}) {
  const t = await getTranslations("plugins");
  const requestLocale = await getLocale();
  const locale = isLocale(requestLocale) ? requestLocale : DEFAULT_LOCALE;
  const result = await resultPromise;

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
      <MarketCatalogItems
        items={items}
        total={total}
        excerpts={items.map((plugin) =>
          markdownToPlainText(localizedPluginDescription(plugin, locale)),
        )}
        servers={servers}
        defaultServerId={serverId}
        canDelete={canDelete}
      />

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
