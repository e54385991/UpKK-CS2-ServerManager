import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { AdditionalFixesConsole } from "@/modules/servers/additional-fixes-console";
import { getServer } from "@/modules/servers/api";
import { parseServerId } from "@/modules/servers/workspace";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.additional-fixes") };
}

export default async function AdditionalFixesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();
  const [t, result] = await Promise.all([getTranslations("serverAdditionalFixes"), getServer(serverId)]);
  if (!result.ok && result.status === 404) notFound();
  if (!result.ok) return <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">{t("fetchError", { status: result.status || "network" })}</Card>;
  return <AdditionalFixesConsole initial={result.data} />;
}
