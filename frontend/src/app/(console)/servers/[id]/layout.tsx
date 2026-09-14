import { Suspense, type ReactNode } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { WORKSPACE_NAMESPACES } from "@/i18n/namespaces";
import { getCurrentServerOperation, getDeploymentLock, getServer } from "@/modules/servers/render-queries";
import { DeleteServerButton } from "@/modules/servers/delete-server-button";
import { WorkspaceStatus, WorkspaceStatusSkeleton } from "@/modules/servers/workspace-status";
import { parseServerId } from "@/modules/servers/workspace";
import { ServerWorkspaceNav } from "@/modules/servers/workspace-nav";
import { SERVER_STATUS_TONE } from "@/modules/servers/types";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";

export default async function ServerWorkspaceLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  const operationPromise = getCurrentServerOperation(serverId);
  const lockPromise = getDeploymentLock(serverId);
  const [t, tServers, result] = await Promise.all([
    getTranslations("serverDetail"),
    getTranslations("servers"),
    getServer(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();

  const server = result.ok ? result.data : null;
  const tone = server ? SERVER_STATUS_TONE[server.status] : null;

  return (
    <ClientMessages namespaces={WORKSPACE_NAMESPACES}>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {server ? server.name : t("title", { id })}
            {server && tone ? (
              <Badge tone={tone}>
                <StatusDot tone={tone} pulse={server.status === "running"} />
                {tServers(`status.${server.status}`)}
              </Badge>
            ) : null}
          </span>
        }
        description={t("workspaceHelp")}
        actions={
          <>
            {server ? (
              <DeleteServerButton
                serverId={server.id}
                name={server.name}
                redirectToList
              />
            ) : null}
            <LinkButton href="/servers" variant="outline">
              {tServers("backToList")}
            </LinkButton>
          </>
        }
      />
      {server ? (
        <Suspense fallback={<WorkspaceStatusSkeleton />}>
          <WorkspaceStatus server={server} operationPromise={operationPromise} lockPromise={lockPromise} />
        </Suspense>
      ) : null}
      <ServerWorkspaceNav serverId={serverId} />
      {children}
    </ClientMessages>
  );
}
