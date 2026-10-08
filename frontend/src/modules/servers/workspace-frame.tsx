import { Suspense, type ReactNode } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { WORKSPACE_NAMESPACES } from "@/i18n/namespaces";
import { getCurrentServerOperation, getDeploymentLock, getServer } from "./render-queries";
import { DeleteServerButton } from "./delete-server-button";
import { WorkspaceStatus, WorkspaceStatusSkeleton } from "./workspace-status";
import { parseServerId, SERVER_WORKSPACE_NAV_ROWS } from "./workspace";
import { ServerWorkspaceNav } from "./workspace-nav";
import { SERVER_STATUS_TONE } from "./types";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Skeleton } from "@/shared/ui/skeleton";

type ServerParams = Promise<{ id: string }>;
async function serverIdFrom(params: ServerParams) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();
  return serverId;
}

export async function WorkspaceFrame({ params, children }: { params: ServerParams; children: ReactNode }) {
  const [t, tServers] = await Promise.all([getTranslations("serverDetail"), getTranslations("servers")]);
  return <ClientMessages namespaces={WORKSPACE_NAMESPACES}>
    <PageHeader
      title={<Suspense fallback={t("workspaceTitle")}><WorkspaceIdentity params={params} /></Suspense>}
      description={t("workspaceHelp")}
      actions={<>
        <Suspense fallback={<Skeleton className="h-9 w-20" />}><WorkspaceDelete params={params} /></Suspense>
        <LinkButton href="/servers" variant="outline">{tServers("backToList")}</LinkButton>
      </>}
    />
    <Suspense fallback={<WorkspaceStatusSkeleton />}><WorkspaceStatusData params={params} /></Suspense>
    <Suspense fallback={<WorkspaceNavigationSkeleton />}><WorkspaceNavigation params={params} /></Suspense>
    {children}
  </ClientMessages>;
}

async function WorkspaceIdentity({ params }: { params: ServerParams }) {
  const serverId = await serverIdFrom(params);
  const [t, tServers, result] = await Promise.all([getTranslations("serverDetail"), getTranslations("servers"), getServer(serverId)]);
  if (!result.ok && result.status === 404) notFound();
  const server = result.ok ? result.data : null;
  const tone = server ? SERVER_STATUS_TONE[server.status] : null;
  return <span className="flex items-center gap-3">
    {server ? server.name : t("title", { id: serverId })}
    {server && tone ? <Badge tone={tone}><StatusDot tone={tone} pulse={server.status === "running"} />{tServers(`status.${server.status}`)}</Badge> : null}
  </span>;
}

async function WorkspaceDelete({ params }: { params: ServerParams }) {
  const result = await getServer(await serverIdFrom(params));
  return result.ok ? <DeleteServerButton serverId={result.data.id} name={result.data.name} redirectToList /> : null;
}

async function WorkspaceStatusData({ params }: { params: ServerParams }) {
  const serverId = await serverIdFrom(params);
  const operationPromise = getCurrentServerOperation(serverId);
  const lockPromise = getDeploymentLock(serverId);
  const result = await getServer(serverId);
  return result.ok ? <WorkspaceStatus server={result.data} operationPromise={operationPromise} lockPromise={lockPromise} /> : null;
}

async function WorkspaceNavigation({ params }: { params: ServerParams }) {
  return <ServerWorkspaceNav serverId={await serverIdFrom(params)} />;
}

async function WorkspaceNavigationSkeleton() {
  const t = await getTranslations("serverWorkspace");
  return <nav aria-label={t("navLabel")} data-testid="server-workspace-nav-loading" className="mb-6 overflow-hidden rounded-lg border border-line bg-surface">
    {SERVER_WORKSPACE_NAV_ROWS.map((row, index) => <div key={row.id} className={`flex items-stretch ${index ? "border-t border-line" : ""}`}>
      <p className="flex w-14 shrink-0 items-center justify-center border-r border-line bg-surface-raised text-[11px] font-semibold tracking-wide text-fg-subtle sm:w-16">{t(`rows.${row.id}`)}</p>
      <ul className="flex min-w-0 flex-1 flex-wrap">
        {row.categories.map(category => <li key={category} className="inline-flex items-center gap-1.5 px-2.5 py-2 text-sm font-medium text-fg-muted">
          <Skeleton className="size-3.5 shrink-0" />{t(`categories.${category}`)}
        </li>)}
      </ul>
    </div>)}
  </nav>;
}
