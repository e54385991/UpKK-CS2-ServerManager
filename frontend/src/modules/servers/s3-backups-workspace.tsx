import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  listOperationLogs,
  listS3Backups,
} from "@/modules/servers/api";
import { S3BackupsConsole } from "@/modules/servers/s3-backups-console";
import type { ServerStatus } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function S3BackupsWorkspace({
  serverId,
  serverStatus,
  gameDirectory,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  gameDirectory: string;
}) {
  const t = await getTranslations("s3Backups");
  const [backups, current, logs, lock] = await Promise.all([
    listS3Backups(serverId),
    getCurrentServerOperation(serverId),
    listOperationLogs(serverId),
    getDeploymentLock(serverId),
  ]);

  if (!backups.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: backups.status || "network" })}</span>
      </Card>
    );
  }

  return (
    <S3BackupsConsole
      serverId={serverId}
      serverStatus={serverStatus}
      gameDirectory={gameDirectory}
      initialBackups={backups.data}
      initialOperation={current.ok ? current.data : null}
      initialLogs={logs.ok ? logs.data : []}
      initialLock={lock.ok ? lock.data : { lockActive: false, serverStatus }}
    />
  );
}

export function S3BackupsWorkspaceSkeleton() {
  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="mb-2 h-4 w-40" />
        <Skeleton className="h-12 w-full" />
      </div>
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="mb-3 h-4 w-32" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}
