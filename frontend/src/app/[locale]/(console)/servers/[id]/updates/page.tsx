import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { UpdatesPanel, UpdatesPanelSkeleton } from "@/modules/updates/updates-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.updates") };
}

async function ServerUpdatesPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<UpdatesPanelSkeleton />}>
      <UpdatesPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerUpdatesPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="updates" fallback={<Loading />}><ServerUpdatesPageData params={params} /></WorkspacePage>;
}
