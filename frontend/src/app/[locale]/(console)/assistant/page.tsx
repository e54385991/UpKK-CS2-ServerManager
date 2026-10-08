import { SessionGate } from "@/modules/shell/session-slots";
import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AssistantPanel, AssistantPanelSkeleton } from "@/modules/assistant/assistant-panel";
import { PageHeader } from "@/shared/ui/page-header";
import { DomainMessages } from "@/i18n/page-messages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("assistant");
  return { title: t("title") };
}

type SearchParams = { conversation?: string; prompt?: string };
export default async function AssistantPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const t = await getTranslations("assistant");
  return <DomainMessages extra={["assistant"]}><section data-route="/assistant">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <PageHeader title={t("title")} description={t("description")} />
    <Suspense fallback={<AssistantPanelSkeleton />}><AssistantData searchParams={searchParams} /></Suspense>
  </section></DomainMessages>;
}
async function AssistantData({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const [{ conversation, prompt }, t] = await Promise.all([searchParams, getTranslations("assistant")]);
  return <AssistantPanel conversationId={conversation} initialDraft={prompt === "crashIsolation" ? t("crashIsolationPrompt") : undefined} />;
}
