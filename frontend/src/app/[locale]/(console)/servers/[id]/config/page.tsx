import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  ServerConfigSection,
  ServerConfigSkeleton,
} from "@/modules/servers/config-workspace";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.config") };
}

async function ServerConfigPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<ServerConfigSkeleton section="game" />}>
      <ServerConfigSection serverId={serverId} section="game" />
    </Suspense>
  );
}

export default function ServerConfigPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="config" fallback={<Loading />}><ServerConfigPageData params={params} /></WorkspacePage>;
}
