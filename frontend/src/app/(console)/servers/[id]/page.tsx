import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { OverviewPanel } from "@/modules/servers/overview-panel";
import { parseServerId } from "@/modules/servers/workspace";
import { Skeleton } from "@/shared/ui/skeleton";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.overview") };
}

export default async function ServerOverviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<OverviewSkeleton />}>
      <OverviewPanel serverId={serverId} />
    </Suspense>
  );
}

function OverviewSkeleton() {
  return (
    <div className="max-w-3xl space-y-3 rounded-lg border border-line bg-surface p-5">
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-3/4" />
    </div>
  );
}
