"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { FrameworksPanel } from "@/modules/servers/frameworks-panel";
import { OperationLiveLog } from "@/modules/servers/operation-live-log";
import { useOperationRunner } from "@/modules/servers/use-operation-runner";
import {
  FRAMEWORK_IDS,
  frameworkIdForAction,
  type FrameworkId,
} from "@/modules/servers/frameworks";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  ServerOperation,
  ServerStatus,
} from "@/modules/servers/types";

export function FrameworksConsole({
  serverId,
  serverStatus,
  initialOperation,
  initialLogs,
  initialLock,
  installedFrameworkKeys,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
  installedFrameworkKeys: readonly FrameworkId[];
}) {
  const t = useTranslations("serverDetail");
  const {
    operation,
    events,
    logRef,
    running,
    busyAction,
    error,
    streamFailed,
    canForceStop,
    runAction,
    refreshAfterForceStop,
  } = useOperationRunner({
    serverId,
    serverStatus,
    initialOperation,
    initialLogs,
    initialLock,
  });
  const emptyHint = useMemo(() => t("frameworks.streamEmpty"), [t]);
  const installedKeys = useMemo(() => {
    const keys = new Set(installedFrameworkKeys);
    if (operation?.status === "completed") {
      const id = frameworkIdForAction(operation.action);
      if (id) keys.add(id);
    }
    return FRAMEWORK_IDS.filter((id) => keys.has(id));
  }, [installedFrameworkKeys, operation]);

  return (
    <div className="space-y-6">
      <FrameworksPanel
        running={running}
        busyAction={busyAction}
        installedKeys={installedKeys}
        onRun={runAction}
      />
      {error ? (
        <p className="rounded-md border border-danger/30 bg-danger-muted/40 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}
      <OperationLiveLog
        serverId={serverId}
        operation={operation}
        events={events}
        logRef={logRef}
        streamFailed={streamFailed}
        canForceStop={canForceStop}
        emptyHint={emptyHint}
        description={t("frameworks.streamHelp")}
        onForceStopDone={refreshAfterForceStop}
      />
    </div>
  );
}
