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

export default async function ServerPluginsPage({
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
