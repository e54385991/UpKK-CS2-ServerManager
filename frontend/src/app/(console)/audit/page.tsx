import { Suspense } from "react";
import type { Metadata } from "next";
import { requireSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import { Card } from "@/shared/ui/card";
import { AuditFilters } from "@/modules/audit/audit-filters";
import { AuditTable, AuditTableSkeleton } from "@/modules/audit/audit-table";

export const metadata: Metadata = { title: "审计日志" };

const PAGE_SIZE = 25;

type SearchParams = {
  category?: string;
  status?: string;
  username?: string;
  offset?: string;
};

export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const session = await requireSession();
  const sp = await searchParams;

  if (!session.isAdmin) {
    return (
      <>
        <PageHeader title="审计日志" description="记录关键操作与安全事件。" />
        <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          仅管理员可查看审计日志。
        </Card>
      </>
    );
  }

  const offset = Math.max(0, Number(sp.offset ?? 0)) || 0;
  const query = {
    category: sp.category,
    status: sp.status,
    username: sp.username,
    limit: PAGE_SIZE,
    offset,
  };

  // A stable key so changing filters/offset remounts Suspense and shows the skeleton.
  const key = JSON.stringify(query);

  return (
    <>
      <PageHeader
        title="审计日志"
        description="记录关键操作与安全事件，支持按分类与状态检索（保留最近 30 天）。"
        actions={<AuditFilters />}
      />
      <Suspense key={key} fallback={<AuditTableSkeleton />}>
        <AuditTable query={query} />
      </Suspense>
    </>
  );
}
