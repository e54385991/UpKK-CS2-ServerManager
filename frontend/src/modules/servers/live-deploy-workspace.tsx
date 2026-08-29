"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { SquareTerminal } from "lucide-react";
import { liveConsoleHref } from "@/modules/console/live-console";
import { OperationLiveLog } from "@/modules/servers/operation-live-log";
import { useOperationRunner } from "@/modules/servers/use-operation-runner";
import {
  isActiveOperation,
  type DeploymentLock,
  type DeploymentLogEntry,
  type ServerOperation,
  type ServerStatus,
} from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";

export function LiveDeployWorkspace({
  serverId,
  serverStatus,
  initialOperation,
  initialLogs,
  initialLock,
  aptMirror,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
  aptMirror: string | null;
}) {
  const t = useTranslations("console");
  const tDetail = useTranslations("serverDetail");
  const router = useRouter();
  const {
    operation,
    events,
    logRef,
    streamFailed,
    canForceStop,
    refreshAfterForceStop,
  } = useOperationRunner({
    serverId,
    serverStatus,
    initialOperation,
    initialLogs,
    initialLock,
    aptMirror,
  });

  const emptyHint = useMemo(() => tDetail("streamEmpty"), [tDetail]);
  const streaming = isActiveOperation(operation);
  const watching = streaming || (!operation && canForceStop);

  function openConsole() {
    router.replace(liveConsoleHref(serverId, "console"));
  }

  return (
    <div className="space-y-3">
      <div
        className={
          watching
            ? "flex flex-wrap items-center justify-between gap-3 rounded-lg border border-info/30 bg-info-muted/40 px-4 py-3 text-sm text-info"
            : "flex flex-wrap items-center justify-between gap-3 rounded-lg border border-ok/30 bg-ok-muted/40 px-4 py-3 text-sm text-ok"
        }
      >
        <p>
          {watching
            ? t("watchingDeploy")
            : operation
              ? t("deployFinished")
              : tDetail("streamIdle")}
        </p>
        {streaming ? null : (
          <Button type="button" size="sm" onClick={openConsole}>
            <SquareTerminal />
            {t("openConsole")}
          </Button>
        )}
      </div>

      <OperationLiveLog
        serverId={serverId}
        operation={operation}
        events={events}
        logRef={logRef}
        streamFailed={streamFailed}
        canForceStop={canForceStop}
        emptyHint={emptyHint}
        showOpenLiveTerminal={false}
        className="min-h-[calc(100dvh-9rem)]"
        logClassName="max-h-[calc(100dvh-15rem)]"
        onForceStopDone={refreshAfterForceStop}
      />
    </div>
  );
}
