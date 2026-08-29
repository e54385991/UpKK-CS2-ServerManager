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

export default async function ServerDiscordPage({
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
