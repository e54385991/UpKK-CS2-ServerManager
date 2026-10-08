import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  InstalledPluginsPanel,
  InstalledPluginsPanelSkeleton,
} from "@/modules/plugins/installed-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.plugins") };
}

async function ServerPluginsPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<InstalledPluginsPanelSkeleton />}>
      <InstalledPluginsPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerPluginsPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="plugins" fallback={<Loading />}><ServerPluginsPageData params={params} /></WorkspacePage>;
}
