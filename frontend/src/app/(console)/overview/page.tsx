import { Suspense } from "react";
import type { Metadata } from "next";
import { getSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import {
  OverviewStats,
  OverviewStatsSkeleton,
} from "@/modules/overview/overview-stats";

export const metadata: Metadata = { title: "总览" };

export default async function OverviewPage() {
  const session = await getSession();
  return (
    <>
      <PageHeader
        title={`欢迎回来${session ? `，${session.username}` : ""}`}
        description="运维态势一览：服务器规模、运行状态与需要关注的告警。"
      />
      <Suspense fallback={<OverviewStatsSkeleton />}>
        <OverviewStats />
      </Suspense>
    </>
  );
}
