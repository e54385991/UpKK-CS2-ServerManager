import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { listInitializedHosts } from "@/modules/servers/setup-api";
import { InitializedHostsManager } from "@/modules/servers/initialized-hosts-manager";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("initializedHosts");
  return { title: t("title") };
}

export default async function InitializedHostsPage() {
  const [t, result] = await Promise.all([
    getTranslations("initializedHosts"),
    listInitializedHosts(),
  ]);

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <LinkButton href="/servers" variant="outline">
              {t("backToServers")}
            </LinkButton>
            <LinkButton href="/servers/new?tab=setup">
              {t("setupNew")}
            </LinkButton>
          </>
        }
      />
      {!result.ok ? (
        <Card className="border-danger/30 bg-danger-muted/40 px-5 py-4 text-sm text-danger">
          {t("fetchError", { status: result.status || "network" })}
        </Card>
      ) : result.data.length === 0 ? (
        <Card className="space-y-3 px-6 py-12 text-center">
          <p className="text-sm font-medium text-fg">{t("emptyTitle")}</p>
          <p className="text-sm text-fg-muted">{t("emptyDescription")}</p>
          <div>
            <LinkButton href="/servers/new?tab=setup">{t("setupNew")}</LinkButton>
          </div>
        </Card>
      ) : (
        <InitializedHostsManager hosts={result.data} />
      )}
    </>
  );
}
