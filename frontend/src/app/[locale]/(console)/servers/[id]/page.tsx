import { WorkspacePage } from "@/modules/servers/workspace-page";
import Loading from "./loading";
import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { PublicRoute as Route } from "@/i18n/public-route";
import { getTranslations } from "next-intl/server";
import { OverviewPanel } from "@/modules/servers/overview-panel";
import { parseServerId } from "@/modules/servers/workspace";
import { LinkButton } from "@/shared/ui/link-button";
import { Skeleton } from "@/shared/ui/skeleton";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.overview") };
}

async function ServerOverviewPageData({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();
  const t = await getTranslations("serverDetail");

  return (
    <>
      <div className="mb-4 flex justify-end">
        <LinkButton
          href={`/servers/new?sourceServerId=${serverId}` as Route}
          variant="outline"
        >
          {t("cloneServer")}
        </LinkButton>
      </div>
      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewPanel serverId={serverId} />
      </Suspense>
    </>
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

export default function ServerOverviewPage({ params }: { params: Promise<{ id: string }>; }) {
  return <WorkspacePage category="overview" fallback={<Loading />}><ServerOverviewPageData params={params} /></WorkspacePage>;
}
