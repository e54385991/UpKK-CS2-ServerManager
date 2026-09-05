import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { listCustomCommands } from "@/modules/commands/api";
import { CommandsConsole } from "@/modules/commands/commands-console";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  listOperationLogs,
} from "@/modules/servers/api";
import { OperationsConsole } from "@/modules/servers/operations-console";
import type { ServerStatus } from "@/modules/servers/types";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function OperationsPanel({
  serverId,
  serverStatus,
  aptMirror,
  gameDirectory,
  clearExecstackEffective,
  clearExecstackOverride,
  execstackFixOnRestart,
  execstackFixTargets,
  osId,
  osVersion,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  aptMirror: string | null;
  gameDirectory: string;
  clearExecstackEffective: boolean;
  clearExecstackOverride: boolean | null;
  execstackFixOnRestart: boolean;
  execstackFixTargets: readonly string[];
  osId: string | null;
  osVersion: string | null;
}) {
  const t = await getTranslations("serverDetail");
  const tCommands = await getTranslations("quickCommands");
  const [current, logs, lock, commands] = await Promise.all([
    getCurrentServerOperation(serverId),
    listOperationLogs(serverId),
    getDeploymentLock(serverId),
    listCustomCommands(serverId),
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
    <div className="space-y-6">
      <OperationsConsole
        serverId={serverId}
        serverStatus={serverStatus}
        initialOperation={current.ok ? current.data : null}
        initialLogs={logs.ok ? logs.data : []}
        initialLock={
          lock.ok ? lock.data : { lockActive: false, serverStatus }
        }
        aptMirror={aptMirror}
        gameDirectory={gameDirectory}
        clearExecstackEffective={clearExecstackEffective}
        clearExecstackOverride={clearExecstackOverride}
        execstackFixOnRestart={execstackFixOnRestart}
        execstackFixTargets={execstackFixTargets}
        osId={osId}
        osVersion={osVersion}
      />
      {commands.ok ? (
        <CommandsConsole serverId={serverId} initial={commands.data} />
      ) : (
        <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          {tCommands("fetchError", { status: commands.status || "network" })}
        </Card>
      )}
    </div>
  );
}

export function OperationsPanelSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
          <Skeleton className="mb-4 h-4 w-32" />
          <Skeleton className="mb-2 h-8 w-full" />
          <Skeleton className="mb-2 h-8 w-full" />
          <Skeleton className="h-8 w-3/4" />
        </div>
        <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
          <Skeleton className="mb-4 h-4 w-40" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    </div>
  );
}
