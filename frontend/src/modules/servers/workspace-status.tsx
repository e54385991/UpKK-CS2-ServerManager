import { isDeployProgressVisible } from "@/modules/console/live-console";
import { SshReconnectCard } from "@/modules/servers/ssh-reconnect-card";
import type { ServerDetail } from "@/modules/servers/api";
import type { getCurrentServerOperation, getDeploymentLock } from "@/modules/servers/render-queries";
import { Skeleton } from "@/shared/ui/skeleton";

export async function WorkspaceStatus({ server, operationPromise, lockPromise }: {
  server: ServerDetail;
  operationPromise: ReturnType<typeof getCurrentServerOperation>;
  lockPromise: ReturnType<typeof getDeploymentLock>;
}) {
  const [currentOperation, lock] = await Promise.all([operationPromise, lockPromise]);
  const operationActive =
    currentOperation.ok &&
    currentOperation.data != null &&
    (currentOperation.data.status === "queued" ||
      currentOperation.data.status === "running");
  const canForceStop =
    operationActive ||
    (lock.ok && lock.data.lockActive) ||
    server.status === "deploying";
  const showDeploy = isDeployProgressVisible({
    serverStatus: server.status,
    lockActive: lock.ok && lock.data.lockActive,
    operation: currentOperation.ok ? currentOperation.data : null,
  });

  return (
    <div data-testid="workspace-status">
      <SshReconnectCard
        serverId={server.id}
        serverName={server.name}
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
    </div>
  );
}

export function WorkspaceStatusSkeleton() {
  return <div data-testid="workspace-status-loading" className="mb-6 rounded-lg border border-line bg-surface p-4" aria-busy="true">
    <Skeleton className="h-7 w-full" />
  </div>;
}
