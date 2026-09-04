import type { Metadata } from "next";
import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { CreateServerForm } from "@/modules/servers/create-form";
import { CloneServerForm } from "@/modules/servers/clone-form";
import { getServerCloneTemplate } from "@/modules/servers/api";
import { getInitializedHostCredentials } from "@/modules/servers/setup-api";
import { SetupWizard } from "@/modules/servers/setup-wizard";
import { cn } from "@/shared/lib/cn";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverNew");
  return { title: t("title") };
}

export default async function NewServerPage({
  searchParams,
}: {
  searchParams: Promise<{
    tab?: string;
    name?: string;
    host?: string;
    sshPort?: string;
    sshUser?: string;
    requireInit?: string;
    from?: string;
    initialized?: string;
    sourceServerId?: string;
  }>;
}) {
  const [t, tServers, tSetup, sp] = await Promise.all([
    getTranslations("serverNew"),
    getTranslations("servers"),
    getTranslations("setupWizard"),
    searchParams,
  ]);
  const tab = sp.tab === "setup" ? "setup" : "create";
  const sshPort = Number(sp.sshPort);
  const sourceServerId = Number(sp.sourceServerId);
  const cloneTemplate =
    tab === "create" && Number.isInteger(sourceServerId) && sourceServerId > 0
      ? await getServerCloneTemplate(sourceServerId)
      : null;
  const prefilled =
    tab === "create" && sp.from
      ? await getInitializedHostCredentials(sp.from)
      : null;
  const initialCredentials = prefilled?.ok ? prefilled.data : undefined;
  return (
    <>
      <PageHeader
        title={tab === "setup" ? tSetup("title") : t("title")}
        description={tab === "setup" ? tSetup("description") : t("description")}
        actions={
          <LinkButton href="/servers" variant="outline">
            {tServers("backToList")}
          </LinkButton>
        }
      />
      <div className="mb-6 flex flex-wrap gap-2" data-testid="new-server-tabs">
        <LinkButton
          href={"/servers/new" as Route}
          variant={tab === "create" ? "primary" : "outline"}
          className={cn(tab === "create" && "pointer-events-none")}
        >
          {t("title")}
        </LinkButton>
        <LinkButton
          href={"/servers/new?tab=setup" as Route}
          variant={tab === "setup" ? "primary" : "outline"}
          className={cn(tab === "setup" && "pointer-events-none")}
        >
          {tSetup("tab")}
        </LinkButton>
      </div>
      {tab === "setup" ? (
        <SetupWizard
          initialName={sp.name ?? ""}
          initialHost={sp.host ?? ""}
          initialSshPort={Number.isFinite(sshPort) && sshPort > 0 ? sshPort : 22}
          initialSshUser={sp.sshUser ?? ""}
          requireInit={sp.requireInit === "1"}
        />
      ) : (
        cloneTemplate?.ok ? (
          <CloneServerForm template={cloneTemplate.data} />
        ) : sp.sourceServerId ? (
          <Card className="border-danger/30 bg-danger-muted/40 px-5 py-4 text-sm text-danger">
            {cloneTemplate?.error ?? t("clone.loadError")}
          </Card>
        ) : (
          <CreateServerForm
            initialCredentials={initialCredentials}
            markedInitializedHost={
              sp.initialized === "1" ? sp.host : initialCredentials?.host
            }
          />
        )
      )}
    </>
  );
}
