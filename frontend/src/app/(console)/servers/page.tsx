import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Plus } from "lucide-react";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { getSession } from "@/modules/auth/session";
import { ServerList, ServerListSkeleton } from "@/modules/servers/server-list";
import { ServerTransferHeader } from "@/modules/servers/transfer-header";
import { SERVER_STATUS_GROUPS } from "@/modules/servers/workspace";
import type { ServerListScope, ServerStatus } from "@/modules/servers/types";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("servers");
  return { title: t("title") };
}

function parseStatus(value: string | undefined): ServerStatus | undefined {
  if (!value) return undefined;
  return (SERVER_STATUS_GROUPS as readonly string[]).includes(value)
    ? (value as ServerStatus)
    : undefined;
}

function parseScope(value: string | undefined): ServerListScope {
  return value === "all" ? "all" : "mine";
}

export default async function ServersPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; scope?: string }>;
}) {
  const [t, sp, session] = await Promise.all([
    getTranslations("servers"),
    searchParams,
    getSession(),
  ]);
  const status = parseStatus(sp.status);
  const scope = session?.isAdmin ? parseScope(sp.scope) : "mine";
  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <Suspense
              fallback={
                <div className="h-9 w-32 animate-pulse rounded-md bg-surface" />
              }
            >
              <ServerTransferHeader />
            </Suspense>
            <LinkButton href="/servers/new?tab=setup" variant="outline">
              {t("setupHost")}
            </LinkButton>
            <LinkButton href="/servers/new">
              <Plus className="size-4" />
              {t("add")}
            </LinkButton>
          </>
        }
      />
      <Suspense
        key={`${scope}:${status ?? "all"}`}
        fallback={<ServerListSkeleton />}
      >
        <ServerList
          status={status}
          scope={scope}
          isAdmin={Boolean(session?.isAdmin)}
        />
      </Suspense>
    </>
  );
}
