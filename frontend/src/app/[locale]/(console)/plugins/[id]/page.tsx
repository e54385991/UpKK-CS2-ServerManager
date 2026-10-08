import { Suspense } from "react";
import Loading from "./loading";
import { SessionGate } from "@/modules/shell/session-slots";
import { getMarketPlugin } from "@/modules/plugins/api";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import type { PublicRoute as Route } from "@/i18n/public-route";
import { requireSession } from "@/modules/auth/render-session";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { PluginDetail } from "@/modules/plugins/plugin-detail";
import { listInstallServers as listServers } from "@/modules/plugins/render-queries";
import { DomainMessages } from "@/i18n/page-messages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("plugins");
  return { title: t("detailTitle") };
}

type SearchParams = {
  serverId?: string;
};

async function PluginDetailData({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const [{ id }, sp, session] = await Promise.all([
    params,
    searchParams,
    requireSession(),
  ]);
  const pluginId = Number(id);
  if (!Number.isInteger(pluginId)) notFound();
  const serverId = Number(sp.serverId);
  const resultPromise = getMarketPlugin(pluginId);
  const serversResult = await listServers(session.isAdmin ? "all" : "mine");
  const servers = serversResult.ok
    ? serversResult.data.map((server) => ({
        id: server.id,
        name: server.name,
        usePanelProxy: server.usePanelProxy,
        githubProxy: server.githubProxy,
      }))
    : [];

  return <PluginDetail resultPromise={resultPromise} pluginId={pluginId}
    serverId={Number.isInteger(serverId) ? serverId : null} servers={servers} canDelete={session.isAdmin} />;
}
export default async function PluginDetailPage(props: { params: Promise<{ id: string }>; searchParams: Promise<SearchParams> }) {
  const t = await getTranslations("plugins");
  return <DomainMessages extra={["plugins"]}><section data-route="/plugins/[id]">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <PageHeader title={t("detailTitle")} description={t("detailHelp")} actions={<LinkButton href={"/plugins" as Route} variant="outline">{t("backToMarket")}</LinkButton>} />
    <Suspense fallback={<Loading />}><PluginDetailData {...props} /></Suspense>
  </section></DomainMessages>;
}
