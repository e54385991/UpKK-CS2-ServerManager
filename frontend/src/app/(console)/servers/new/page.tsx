import type { Metadata } from "next";
import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { CreateServerForm } from "@/modules/servers/create-form";
import { getInitializedHostCredentials } from "@/modules/servers/setup-api";
import { SetupWizard } from "@/modules/servers/setup-wizard";
import { cn } from "@/shared/lib/cn";

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
        <CreateServerForm
          initialCredentials={initialCredentials}
          markedInitializedHost={
            sp.initialized === "1" ? sp.host : initialCredentials?.host
          }
        />
      )}
    </>
  );
}
