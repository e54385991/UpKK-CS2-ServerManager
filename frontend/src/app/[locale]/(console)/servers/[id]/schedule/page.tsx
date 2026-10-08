import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { SchedulePanel, SchedulePanelSkeleton } from "@/modules/schedule/schedule-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.schedule") };
}

async function ServerSchedulePageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<SchedulePanelSkeleton />}>
      <SchedulePanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerSchedulePage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="schedule" fallback={<Loading />}><ServerSchedulePageData params={params} /></WorkspacePage>;
}
