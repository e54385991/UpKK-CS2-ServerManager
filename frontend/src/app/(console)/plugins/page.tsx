import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("plugins");
  return { title: t("title") };
}

export default async function PluginsPage() {
  const t = await getTranslations("plugins");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <ModulePlaceholder phase={t("phase")}>{t("body")}</ModulePlaceholder>
    </>
  );
}
