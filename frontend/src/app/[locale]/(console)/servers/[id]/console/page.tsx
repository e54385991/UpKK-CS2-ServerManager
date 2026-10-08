import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ConsolePanel, ConsolePanelSkeleton } from "@/modules/console/console-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.console") };
}

async function ServerConsolePageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<ConsolePanelSkeleton />}>
      <ConsolePanel serverId={serverId} />
    </Suspense>
  );
}

export default function ServerConsolePage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="console" fallback={<Loading />}><ServerConsolePageData params={params} /></WorkspacePage>;
}
