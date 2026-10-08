import { Suspense } from "react";
import Loading from "./loading";
import { SessionGate } from "@/modules/shell/session-slots";
import type { Metadata } from "next";
import type { PublicRoute as Route } from "@/i18n/public-route";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { CreateServerForm } from "@/modules/servers/create-form";
import { CloneServerForm, SetupWizard } from "@/modules/servers/new-server-forms";
import { getServerCloneTemplate } from "@/modules/servers/api";
import { getInitializedHostCredentials } from "@/modules/servers/setup-api";
import { cn } from "@/shared/lib/cn";
import { Card } from "@/shared/ui/card";
import { ServersSectionMessages } from "@/i18n/page-messages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverNew");
  return { title: t("title") };
}

async function NewServerData({
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
  const [t, tSetup, sp] = await Promise.all([
    getTranslations("serverNew"),
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
    <ServersSectionMessages>
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
    </ServersSectionMessages>
  );
}

type SearchParams = {
    tab?: string;
    name?: string;
    host?: string;
    sshPort?: string;
    sshUser?: string;
    requireInit?: string;
    from?: string;
    initialized?: string;
    sourceServerId?: string;
  };
export default async function NewServerPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const [t, tServers] = await Promise.all([getTranslations("serverNew"), getTranslations("servers")]);
  return <ServersSectionMessages><section data-route="/servers/new">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <PageHeader title={<Suspense fallback={t("title")}><NewServerTitle searchParams={searchParams} /></Suspense>}
      description={<Suspense fallback={t("description")}><NewServerDescription searchParams={searchParams} /></Suspense>}
      actions={<LinkButton href="/servers" variant="outline">{tServers("backToList")}</LinkButton>} />
    <Suspense fallback={<Loading />}><NewServerData searchParams={searchParams} /></Suspense>
  </section></ServersSectionMessages>;
}
async function NewServerTitle({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams; const t = await getTranslations(sp.tab === "setup" ? "setupWizard" : "serverNew");
  return t("title");
}
async function NewServerDescription({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams; const t = await getTranslations(sp.tab === "setup" ? "setupWizard" : "serverNew");
  return t("description");
}
