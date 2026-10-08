import { SessionGate } from "@/modules/shell/session-slots";
import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import {
  ProfilePanel,
  ProfilePanelSkeleton,
} from "@/modules/profile/profile-panel";
import { PageHeader } from "@/shared/ui/page-header";

export const maxDuration = 180;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("profile");
  return { title: t("title") };
}

export default async function ProfilePage() {
  const t = await getTranslations("profile");

  return (
    <section data-route="/settings/profile">
      <Suspense fallback={null}><SessionGate /></Suspense>
      <PageHeader title={t("title")} description={t("description")} />
      <Suspense fallback={<ProfilePanelSkeleton />}>
        <ProfilePanel />
      </Suspense>
    </section>
  );
}
