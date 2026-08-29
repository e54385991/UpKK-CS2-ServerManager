import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import type { Route } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { PluginDetail } from "@/modules/plugins/plugin-detail";
import { listServers } from "@/modules/servers/api";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("plugins");
  return { title: t("detailTitle") };
}

type SearchParams = {
  serverId?: string;
};

export default async function PluginDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const [{ id }, sp, t] = await Promise.all([
    params,
    searchParams,
    getTranslations("plugins"),
  ]);
  const pluginId = Number(id);
  if (!Number.isInteger(pluginId)) notFound();
  const serverId = Number(sp.serverId);
  const serversResult = await listServers();
  const servers = serversResult.ok
    ? serversResult.data.map((server) => ({
        id: server.id,
        name: server.name,
        usePanelProxy: server.usePanelProxy,
        githubProxy: server.githubProxy,
      }))
    : [];

  return (
    <>
      <PageHeader
        title={t("detailTitle")}
        description={t("detailHelp")}
        actions={
          <LinkButton href={"/plugins" as Route} variant="outline">
            {t("backToMarket")}
          </LinkButton>
        }
      />
      <PluginDetail
        pluginId={pluginId}
        serverId={Number.isInteger(serverId) ? serverId : null}
        servers={servers}
      />
    </>
  );
}