import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { MonitoringWorkspace } from "@/modules/servers/monitoring-workspace";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.monitoring") };
}

async function ServerMonitoringPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return <MonitoringWorkspace serverId={serverId} />;
}

export default function ServerMonitoringPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="monitoring" fallback={<Loading />}><ServerMonitoringPageData params={params} /></WorkspacePage>;
}
