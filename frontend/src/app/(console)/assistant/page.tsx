import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AssistantPanel, AssistantPanelSkeleton } from "@/modules/assistant/assistant-panel";
import { PageHeader } from "@/shared/ui/page-header";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("assistant");
  return { title: t("title") };
}

export default async function AssistantPage({
  searchParams,
}: {
  searchParams: Promise<{ conversation?: string }>;
}) {
  const [{ conversation }, t] = await Promise.all([
    searchParams,
    getTranslations("assistant"),
  ]);

  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <Suspense fallback={<AssistantPanelSkeleton />}>
        <AssistantPanel conversationId={conversation} />
      </Suspense>
    </>
  );
}
