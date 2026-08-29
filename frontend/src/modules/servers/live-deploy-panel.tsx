import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  listOperationLogs,
} from "@/modules/servers/api";
import { LiveDeployWorkspace } from "@/modules/servers/live-deploy-workspace";
import type { ServerStatus } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function LiveDeployPanel({
  serverId,
  serverStatus,
  aptMirror,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  aptMirror: string | null;
}) {
  const t = await getTranslations("serverDetail");
  const [current, logs, lock] = await Promise.all([
    getCurrentServerOperation(serverId),
    listOperationLogs(serverId),
    getDeploymentLock(serverId),
  ]);

  if (!current.ok && !logs.ok && !lock.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>
          {t("fetchError", {
            status: current.status || logs.status || lock.status || "network",
          })}
        </span>
      </Card>
    );
  }

  return (
    <LiveDeployWorkspace
      serverId={serverId}
      serverStatus={serverStatus}
      initialOperation={current.ok ? current.data : null}
      initialLogs={logs.ok ? logs.data : []}
      initialLock={lock.ok ? lock.data : { lockActive: false, serverStatus }}
      aptMirror={aptMirror}
    />
  );
}

export function LiveDeployPanelSkeleton() {
  return (
    <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="mb-4 h-4 w-40" />
      <Skeleton className="h-[28rem] w-full" />
    </div>
  );
}
