import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  ServerDiscordPanel,
  ServerDiscordPanelSkeleton,
} from "@/modules/discord/server-discord-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.discord") };
}

async function ServerDiscordPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<ServerDiscordPanelSkeleton />}>
      <ServerDiscordPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerDiscordPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="discord" fallback={<Loading />}><ServerDiscordPageData params={params} /></WorkspacePage>;
}
