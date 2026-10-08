import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { MapsPanel, MapsPanelSkeleton } from "@/modules/maps/maps-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.maps") };
}

async function ServerMapsPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<MapsPanelSkeleton />}>
      <MapsPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerMapsPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="maps" fallback={<Loading />}><ServerMapsPageData params={params} /></WorkspacePage>;
}
