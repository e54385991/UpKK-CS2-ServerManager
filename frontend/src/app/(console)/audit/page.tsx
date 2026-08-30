import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { requireSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import { Card } from "@/shared/ui/card";
import { AuditFilters } from "@/modules/audit/audit-filters";
import { AuditTable, AuditTableSkeleton } from "@/modules/audit/audit-table";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("audit");
  return { title: t("title") };
}

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
  const [session, t, sp] = await Promise.all([
    requireSession(),
    getTranslations("audit"),
    searchParams,
  ]);

  if (!session.isAdmin) {
    return (
      <>
        <PageHeader title={t("title")} description={t("description")} />
        <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          {t("forbidden")}
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
        title={t("title")}
        description={t("description")}
        actions={<AuditFilters />}
      />
      <Suspense key={key} fallback={<AuditTableSkeleton />}>
        <AuditTable query={query} />
      </Suspense>
    </>
  );
}
