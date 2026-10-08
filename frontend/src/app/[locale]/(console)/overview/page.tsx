import { SessionGate } from "@/modules/shell/session-slots";
import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { getSession } from "@/modules/auth/render-session";
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
  const t = await getTranslations("overview");
  return <section data-route="/overview">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <PageHeader title={<Suspense fallback={t("welcomeNoName")}><Greeting /></Suspense>} description={t("description")} />
    <Suspense fallback={<OverviewStatsSkeleton />}><OverviewStats /></Suspense>
  </section>;
}
async function Greeting() {
  const [session, t] = await Promise.all([getSession(), getTranslations("overview")]);
  return session ? t("welcome", { name: session.username }) : t("welcomeNoName");
}
