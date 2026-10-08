import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  PluginConfigsPanel,
  PluginConfigsPanelSkeleton,
} from "@/modules/plugin-configs/plugin-configs-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.plugin-configs") };
}

async function ServerPluginConfigsPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<PluginConfigsPanelSkeleton />}>
      <PluginConfigsPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerPluginConfigsPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="plugin-configs" fallback={<Loading />}><ServerPluginConfigsPageData params={params} /></WorkspacePage>;
}
