import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  GameModesPanel,
  GameModesPanelSkeleton,
} from "@/modules/game-modes/game-modes-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.game-modes") };
}

export default async function ServerGameModesPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<GameModesPanelSkeleton />}>
      <GameModesPanel serverId={serverId} />
    </Suspense>
  );
}
