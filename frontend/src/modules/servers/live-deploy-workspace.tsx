"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { isDeployProgressVisible } from "@/modules/console/live-console";
import { LiveConsolePopups } from "@/modules/console/open-live-terminal";
import { SessionPane } from "@/modules/console/session-pane";
import { useConsolePane } from "@/modules/console/use-console-pane";
import type { ConsolePane } from "@/modules/console/types";
import { paneDisplayText } from "@/modules/console/pane-client";
import { OperationLiveLog } from "@/modules/servers/operation-live-log";
import { useOperationRunner } from "@/modules/servers/use-operation-runner";
import {
  isActiveOperation,
  type DeploymentLock,
  type DeploymentLogEntry,
  type ServerOperation,
  type ServerStatus,
} from "@/modules/servers/types";

export function LiveDeployWorkspace({
  serverId,
  serverStatus,
  initialOperation,
  initialLogs,
  initialLock,
  aptMirror,
  initialPane = null,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
  aptMirror: string | null;
  initialPane?: ConsolePane | null;
}) {
  const t = useTranslations("console");
  const tDetail = useTranslations("serverDetail");
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

  const pane = useConsolePane({
    serverId,
    kind: "steamcmd",
    initial: initialPane,
    enabled: true,
  });
  const paneText = paneDisplayText(pane);
  const paneReady = Boolean(pane?.running || paneText);
  const emptyHint = useMemo(
    () => (paneReady ? t("paneAlive") : tDetail("streamEmpty")),
    [paneReady, t, tDetail],
  );
  const streaming = isActiveOperation(operation);
  const watching = streaming || (!operation && canForceStop) || Boolean(pane?.running);

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
          <LiveConsolePopups
            serverId={serverId}
            showDeploy={isDeployProgressVisible({
              serverStatus,
              steamcmdRunning: Boolean(pane?.running),
              operation,
            })}
          />
        )}
      </div>

      <SessionPane
        pane={pane}
        emptyText={pane?.running ? t("paneAlive") : t("paneIdle")}
        className="min-h-[calc(100dvh-22rem)]"
      />

      <OperationLiveLog
        serverId={serverId}
        operation={operation}
        events={events}
        logRef={logRef}
        streamFailed={streamFailed}
        canForceStop={canForceStop}
        emptyHint={emptyHint}
        showOpenLiveTerminal={false}
        className="min-h-64"
        logClassName="max-h-64"
        onForceStopDone={refreshAfterForceStop}
      />
    </div>
  );
}
