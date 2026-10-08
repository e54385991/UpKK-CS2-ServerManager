import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  CleanupPanel,
  CleanupPanelSkeleton,
} from "@/modules/cleanup/cleanup-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.cleanup") };
}

async function ServerCleanupPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<CleanupPanelSkeleton />}>
      <CleanupPanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerCleanupPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="cleanup" fallback={<Loading />}><ServerCleanupPageData params={params} /></WorkspacePage>;
}
