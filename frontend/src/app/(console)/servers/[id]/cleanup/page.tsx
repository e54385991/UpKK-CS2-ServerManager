import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  CleanupConsole,
  CleanupPanelSkeleton,
} from "@/modules/cleanup/cleanup-console";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.cleanup") };
}

export default async function ServerCleanupPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<CleanupPanelSkeleton />}>
      <CleanupConsole serverId={serverId} />
    </Suspense>
  );
}
