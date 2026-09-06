import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { requireSession } from "@/modules/auth/session";
import { PluginCatalogButton } from "@/modules/plugins/catalog-button";
import { GitHubInstallButton } from "@/modules/plugins/github-install-button";
import { FrameworkTabs } from "@/modules/plugins/framework-tabs";
import { MarketFilters } from "@/modules/plugins/market-filters";
import { MarketPluginCreateButton } from "@/modules/plugins/market-create-button";
import {
  MarketCatalog,
  MarketCatalogSkeleton,
} from "@/modules/plugins/market-catalog";
import { SyncDescriptionsButton } from "@/modules/plugins/sync-descriptions-button";
import { toPluginFramework } from "@/modules/plugins/types";
import { listServers } from "@/modules/servers/api";
import { PageHeader } from "@/shared/ui/page-header";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("plugins");
  return { title: t("title") };
}

const PAGE_SIZE = 20;

type SearchParams = {
  q?: string;
  category?: string;
  framework?: string;
  offset?: string;
  serverId?: string;
};

export default async function PluginsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const [t, sp, session, serversResult] = await Promise.all([
    getTranslations("plugins"),
    searchParams,
    requireSession(),
    listServers(),
  ]);
  const offset = Math.max(0, Number(sp.offset ?? 0)) || 0;
  const serverId = Number(sp.serverId);
  // The marketplace opens on the CounterStrikeSharp section; SwiftlyS2 is the
  // other top-level tab.
  const framework = toPluginFramework(sp.framework?.trim());
  const query = {
    q: sp.q?.trim() || undefined,
    category: sp.category?.trim() || undefined,
    framework,
    limit: PAGE_SIZE,
    offset,
  };
  const key = JSON.stringify({ ...query, serverId: sp.serverId ?? null });

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <GitHubInstallButton
              servers={
                serversResult.ok
                  ? serversResult.data.map((server) => ({
                      id: server.id,
                      name: server.name,
                      usePanelProxy: server.usePanelProxy,
                      githubProxy: server.githubProxy,
                    }))
                  : []
              }
              defaultServerId={Number.isInteger(serverId) ? serverId : null}
            />
            {session.isAdmin ? <MarketPluginCreateButton /> : null}
            {session.isAdmin ? (
              <SyncDescriptionsButton framework={framework} />
            ) : null}
            <PluginCatalogButton canImport={session.isAdmin} />
            <MarketFilters />
          </>
        }
      />
      <FrameworkTabs
        active={framework}
        query={query}
        serverId={Number.isInteger(serverId) ? serverId : undefined}
      />
      <Suspense key={key} fallback={<MarketCatalogSkeleton />}>
        <MarketCatalog
          query={query}
          serverId={Number.isInteger(serverId) ? serverId : undefined}
          canDelete={session.isAdmin}
          servers={
            serversResult.ok
              ? serversResult.data.map((server) => ({
                  id: server.id,
                  name: server.name,
                  usePanelProxy: server.usePanelProxy,
                  githubProxy: server.githubProxy,
                }))
              : []
          }
        />
      </Suspense>
    </>
  );
}
