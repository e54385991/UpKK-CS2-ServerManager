import { SessionGate } from "@/modules/shell/session-slots";
import { Skeleton } from "@/shared/ui/skeleton";
import { listMarketPlugins } from "@/modules/plugins/api";
import { AIImportButton } from "@/modules/plugins/ai-import-button";
import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { requireSession } from "@/modules/auth/render-session";
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
import { toMarketSort, toPluginFrameworkSection } from "@/modules/plugins/types";
import { listInstallServers as listServers } from "@/modules/plugins/render-queries";
import { PageHeader } from "@/shared/ui/page-header";
import { DomainMessages } from "@/i18n/page-messages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("plugins");
  return { title: t("title") };
}

const PAGE_SIZE = 20;

type SearchParams = {
  q?: string;
  category?: string;
  framework?: string;
  sort?: string;
  offset?: string;
  serverId?: string;
};

function queryFrom(sp: SearchParams) {
  const offset = Math.max(0, Number(sp.offset ?? 0)) || 0;
  return { q: sp.q?.trim() || undefined, category: sp.category?.trim() || undefined,
    framework: toPluginFrameworkSection(sp.framework?.trim()), sort: toMarketSort(sp.sort?.trim()),
    limit: PAGE_SIZE, offset };
}
export default async function PluginsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const t = await getTranslations("plugins");
  return <DomainMessages extra={["plugins"]}><section data-route="/plugins">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <PageHeader title={t("title")} description={t("description")} actions={
      <Suspense fallback={<Skeleton className="h-9 w-64" />}><MarketControls searchParams={searchParams} /></Suspense>
    } />
    <Suspense fallback={<FrameworkTabsSkeleton />}><MarketTabs searchParams={searchParams} /></Suspense>
    <Suspense fallback={<MarketCatalogSkeleton />}><MarketData searchParams={searchParams} /></Suspense>
  </section></DomainMessages>;
}
async function MarketControls({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const [sp, session] = await Promise.all([searchParams, requireSession()]);
  const serversResult = await listServers(session.isAdmin ? "all" : "mine");
  const serverId = Number(sp.serverId);
  return <>
    <GitHubInstallButton servers={serversResult.ok ? serversResult.data.map(server => ({ id: server.id,
      name: server.name, usePanelProxy: server.usePanelProxy, githubProxy: server.githubProxy })) : []}
      defaultServerId={Number.isInteger(serverId) ? serverId : null} />
    {session.isAdmin ? <MarketPluginCreateButton /> : null}
    {session.isAdmin ? <AIImportButton /> : null}
    {session.isAdmin ? <SyncDescriptionsButton framework={queryFrom(sp).framework} /> : null}
    <PluginCatalogButton canImport={session.isAdmin} />
    <MarketFilters />
  </>;
}
async function MarketTabs({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const query = queryFrom(sp);
  const serverId = Number(sp.serverId);
  return <FrameworkTabs active={query.framework} query={query} serverId={Number.isInteger(serverId) ? serverId : undefined} />;
}
async function MarketData({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const [sp, session] = await Promise.all([searchParams, requireSession()]);
  const query = queryFrom(sp);
  const serverId = Number(sp.serverId);
  const resultPromise = listMarketPlugins(query);
  const serversResult = await listServers(session.isAdmin ? "all" : "mine");
  return <Suspense key={JSON.stringify({ ...query, serverId: sp.serverId ?? null })} fallback={<MarketCatalogSkeleton />}>
    <MarketCatalog resultPromise={resultPromise} query={query} serverId={Number.isInteger(serverId) ? serverId : undefined}
      canDelete={session.isAdmin} servers={serversResult.ok ? serversResult.data.map(server => ({
        id: server.id, name: server.name, usePanelProxy: server.usePanelProxy, githubProxy: server.githubProxy })) : []} />
  </Suspense>;
}
async function FrameworkTabsSkeleton() {
  const t = await getTranslations("plugins");
  return <nav aria-label={t("frameworkSections")} className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface-overlay p-1">
    {(["counterstrikesharp", "swiftly", "other"] as const).map(framework => <span key={framework} className="rounded-md px-3 py-1.5 text-sm font-medium text-fg-muted">{t(`frameworks.${framework}`)}</span>)}
  </nav>;
}
