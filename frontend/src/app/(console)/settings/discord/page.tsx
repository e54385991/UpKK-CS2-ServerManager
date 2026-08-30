import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { DiscordPanel, DiscordPanelSkeleton } from "@/modules/discord/discord-panel";
import { PageHeader } from "@/shared/ui/page-header";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("discord");
  return { title: t("title") };
}

export default async function DiscordSettingsPage() {
  const t = await getTranslations("discord");
  return (
    <>
      <PageHeader title={t("title")} description={t("help")} />
      <Suspense fallback={<DiscordPanelSkeleton />}>
        <DiscordPanel />
      </Suspense>
    </>
  );
}
