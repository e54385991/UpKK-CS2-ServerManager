import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { requireSession } from "@/modules/auth/session";
import { SettingsPanel, SettingsPanelSkeleton } from "@/modules/settings/settings-panel";
import { PageHeader } from "@/shared/ui/page-header";
import { Badge } from "@/shared/ui/badge";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("settings");
  return { title: t("title") };
}

export default async function SettingsPage() {
  const [session, t] = await Promise.all([
    requireSession(),
    getTranslations("settings"),
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

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<Badge tone="danger">{t("adminOnly")}</Badge>}
      />
      <Suspense fallback={<SettingsPanelSkeleton />}>
        <SettingsPanel />
      </Suspense>
    </>
  );
}
