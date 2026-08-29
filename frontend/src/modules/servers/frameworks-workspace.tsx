import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { listServerPlugins } from "@/modules/plugins/api";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  listOperationLogs,
} from "@/modules/servers/api";
import { FrameworksConsole } from "@/modules/servers/frameworks-console";
import { detectInstalledFrameworkKeys } from "@/modules/servers/frameworks";
import type { ServerStatus } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function FrameworksWorkspace({
  serverId,
  serverStatus,
}: {
  serverId: number;
  serverStatus: ServerStatus;
}) {
  const t = await getTranslations("serverDetail");
  const [current, logs, lock, plugins] = await Promise.all([
    getCurrentServerOperation(serverId),
    listOperationLogs(serverId),
    getDeploymentLock(serverId),
    listServerPlugins(serverId),
  ]);

  if (!current.ok && !logs.ok && !lock.ok && !plugins.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>
          {t("fetchError", {
            status:
              current.status ||
              logs.status ||
              lock.status ||
              plugins.status ||
              "network",
          })}
        </span>
      </Card>
    );
  }

  return (
    <FrameworksConsole
      serverId={serverId}
      serverStatus={serverStatus}
      initialOperation={current.ok ? current.data : null}
      initialLogs={logs.ok ? logs.data : []}
      initialLock={
        lock.ok ? lock.data : { lockActive: false, serverStatus }
      }
      installedFrameworkKeys={
        plugins.ok ? detectInstalledFrameworkKeys(plugins.data) : []
      }
    />
  );
}

export function FrameworksWorkspaceSkeleton() {
  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="mb-2 h-4 w-40" />
        <Skeleton className="h-12 w-full" />
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
    </div>
  );
}
