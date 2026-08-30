import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { getSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import {
  OverviewStats,
  OverviewStatsSkeleton,
} from "@/modules/overview/overview-stats";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("nav");
  return { title: t("overview") };
}

export default async function OverviewPage() {
  const [session, t] = await Promise.all([
    getSession(),
    getTranslations("overview"),
  ]);
  return (
    <>
      <PageHeader
        title={
          session
            ? t("welcome", { name: session.username })
            : t("welcomeNoName")
        }
        description={t("description")}
      />
      <Suspense fallback={<OverviewStatsSkeleton />}>
        <OverviewStats />
      </Suspense>
    </>
  );
}
