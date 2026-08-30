import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  getServer,
} from "@/modules/servers/api";
import { isDeployProgressVisible } from "@/modules/console/live-console";
import { DeleteServerButton } from "@/modules/servers/delete-server-button";
import { SshReconnectCard } from "@/modules/servers/ssh-reconnect-card";
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

  const [t, tServers] = await Promise.all([
    getTranslations("serverDetail"),
    getTranslations("servers"),
  ]);
  const [result, currentOperation, lock] = await Promise.all([
    getServer(serverId),
    getCurrentServerOperation(serverId),
    getDeploymentLock(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();

  const server = result.ok ? result.data : null;
  const tone = server ? SERVER_STATUS_TONE[server.status] : null;
  const operationActive =
    currentOperation.ok &&
    currentOperation.data != null &&
    (currentOperation.data.status === "queued" ||
      currentOperation.data.status === "running");
  const canForceStop =
    operationActive ||
    (lock.ok && lock.data.lockActive) ||
    server?.status === "deploying";
  const showDeploy = isDeployProgressVisible({
    serverStatus: server?.status ?? null,
    lockActive: lock.ok && lock.data.lockActive,
    operation: currentOperation.ok ? currentOperation.data : null,
  });

  return (
    <>
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
        <SshReconnectCard
          serverId={server.id}
          isSshDown={server.isSshDown}
          sshPooled={server.sshPooled}
          sshInUse={server.sshInUse}
          sshActiveLeases={server.sshActiveLeases}
          sshIdleSeconds={server.sshIdleSeconds}
          canForceStop={canForceStop}
          showDeploy={showDeploy}
          health={{
            id: server.id,
            isSshDown: server.isSshDown,
            sshHealthStatus: server.sshHealthStatus,
            consecutiveSshFailures: server.consecutiveSshFailures,
            sshHealthFailureThreshold: server.sshHealthFailureThreshold,
            sshHealthCheckIntervalHours: server.sshHealthCheckIntervalHours,
            lastSshHealthCheck: server.lastSshHealthCheck,
          }}
        />
      ) : null}
      <ServerWorkspaceNav serverId={serverId} />
      {children}
    </>
  );
}
