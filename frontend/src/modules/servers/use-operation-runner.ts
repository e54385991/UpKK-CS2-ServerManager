"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  refreshCurrentOperationAction,
  refreshDeploymentLockAction,
  refreshOperationJournalAction,
  refreshOperationLogsAction,
  refreshServerAction,
  applyAptMirrorAction,
  restoreS3BackupAction,
  startServerOperationAction,
} from "@/modules/servers/actions";
import { toAptMirror, type AptMirrorId } from "@/modules/servers/apt-mirrors";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm } from "@/shared/feedback";
import {
  mergeOperationEvents,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import {
  CONFIRM_ACTIONS,
  type DeploymentLock,
  type DeploymentLogEntry,
  type OperationStreamEvent,
  isActiveOperation,
  type ServerOperation,
  type ServerOperationAction,
  type ServerStatus,
} from "@/modules/servers/types";

export {
  OPERATION_EVENT_LIMIT,
  mergeOperationEvents,
  parseOperationEvent,
} from "@/modules/servers/operation-events";

export { isActiveOperation };

export function operationEventsUrl(
  serverId: number,
  operationId: string,
): string {
  return `/ops-stream/servers/${serverId}/operations/${operationId}?after=0`;
}

export function useOperationRunner({
  serverId,
  serverStatus,
  initialOperation,
  initialLogs,
  initialLock,
  aptMirror = null,
  onSettled,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
  aptMirror?: string | null;
  onSettled?: () => void | Promise<void>;
}) {
  const t = useTranslations("serverDetail");
  const router = useRouter();
  const logRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState(serverStatus);
  const [operation, setOperation] = useState(initialOperation);
  const [logs, setLogs] = useState(initialLogs);
  const [lock, setLock] = useState(initialLock);
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);
  const [busyAction, setBusyAction] = useState<ServerOperationAction | null>(
    null,
  );
  const [busyMirror, setBusyMirror] = useState<AptMirrorId | null>(null);
  const [currentMirror, setCurrentMirror] = useState(toAptMirror(aptMirror));
  const [error, setError] = useState<string | null>(null);
  const [streamFailed, setStreamFailed] = useState(false);
  const onSettledRef = useRef(onSettled);
  const operationRef = useRef(operation);
  useEffect(() => {
    onSettledRef.current = onSettled;
  }, [onSettled]);
  useEffect(() => {
    operationRef.current = operation;
  }, [operation]);

  const running =
    isActiveOperation(operation) || busyAction !== null || busyMirror !== null;
  const operationId = operation?.operationId ?? null;
  const canForceStop =
    isActiveOperation(operation) || lock.lockActive || status === "deploying";

  const refreshAfterTerminal = useCallback(async () => {
    const serverResult = await refreshServerAction(serverId);
    const logsResult = await refreshOperationLogsAction(serverId);
    const lockResult = await refreshDeploymentLockAction(serverId);
    if (serverResult.ok) {
      setStatus(serverResult.data.status);
      setCurrentMirror(toAptMirror(serverResult.data.aptMirror));
    }
    if (logsResult.ok) setLogs(logsResult.data);
    if (lockResult.ok) setLock(lockResult.data);
    router.refresh();
    await onSettledRef.current?.();
  }, [router, serverId]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  useEffect(() => {
    if (!operationId) return;
    let cancelled = false;
    const pull = async () => {
      const result = await refreshOperationJournalAction(serverId, operationId);
      if (cancelled || !result.ok) return;
      if (result.data.operation.operationId !== operationId) return;
      setOperation(result.data.operation);
      setEvents((current) => mergeOperationEvents(current, result.data.events));
      if (result.data.events.length > 0) setStreamFailed(false);
    };
    void pull();
    const id = window.setInterval(() => void pull(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [operationId, serverId]);

  useEffect(() => {
    if (!operationId) return;
    const alreadyTerminal = !isActiveOperation(operationRef.current);
    const source = new EventSource(operationEventsUrl(serverId, operationId));
    source.onopen = () => {
      setStreamFailed(false);
    };
    const seen = new Set<string>();
    let received = false;
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event || (event.sequence && seen.has(event.sequence))) return null;
      if (event.sequence) seen.add(event.sequence);
      received = true;
      setEvents((current) => mergeOperationEvents(current, [event]));
      return event;
    };
    source.onmessage = (message) => {
      ingest(message.data);
    };
    const onNamed = (message: MessageEvent<string>) => {
      const event = ingest(message.data);
      if (
        event &&
        (event.type === "operation_completed" ||
          event.type === "operation_failed")
      ) {
        setOperation((current) =>
          current
            ? {
                ...current,
                status:
                  event.type === "operation_completed" ? "completed" : "failed",
                success: event.type === "operation_completed",
                message: event.message,
                completedAt: event.timestamp,
                serverStatus: event.serverStatus
                  ? (event.serverStatus as ServerStatus)
                  : current.serverStatus,
              }
            : current,
        );
        if (!alreadyTerminal) void refreshAfterTerminal();
      }
    };
    source.addEventListener("progress", onNamed);
    source.addEventListener("operation_completed", onNamed);
    source.addEventListener("operation_failed", onNamed);
    source.onerror = () => {
      if (!alreadyTerminal) return;
      source.close();
      if (!received) setStreamFailed(true);
    };
    return () => source.close();
  }, [operationId, refreshAfterTerminal, serverId]);

  useEffect(() => {
    if (operationId) return;
    if (status !== "deploying" && !lock.lockActive) return;
    let cancelled = false;
    const tick = async () => {
      const result = await refreshCurrentOperationAction(serverId);
      if (cancelled || !result.ok || !result.data) return;
      setOperation(result.data);
    };
    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [lock.lockActive, operationId, serverId, status]);

  async function runAction(action: ServerOperationAction) {
    if (running) return;
    if (CONFIRM_ACTIONS.has(action) && !(await confirm(t(`confirm.${action}`)))) {
      return;
    }
    setError(null);
    setStreamFailed(false);
    setBusyAction(action);
    const result = await startServerOperationAction(serverId, action);
    setBusyAction(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setEvents([]);
    setOperation(result.data);
    trackQueuedOperation(result.data);
  }

  async function runS3Restore(objectKey: string) {
    if (running) return;
    setError(null);
    setStreamFailed(false);
    setBusyAction("s3_restore");
    const result = await restoreS3BackupAction(serverId, objectKey);
    setBusyAction(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setEvents([]);
    setOperation(result.data);
    trackQueuedOperation(result.data);
  }

  async function onSwitchMirror(mirror: AptMirrorId) {
    if (running) return;
    setError(null);
    setStreamFailed(false);
    setBusyMirror(mirror);
    const result = await applyAptMirrorAction(serverId, mirror);
    setBusyMirror(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setEvents([]);
    setCurrentMirror(mirror);
    setOperation(result.data);
    trackQueuedOperation(result.data);
  }

  async function refreshAfterForceStop() {
    const [current, lockResult, logsResult, serverResult] = await Promise.all([
      refreshCurrentOperationAction(serverId),
      refreshDeploymentLockAction(serverId),
      refreshOperationLogsAction(serverId),
      refreshServerAction(serverId),
    ]);
    if (current.ok) setOperation(current.data);
    if (lockResult.ok) setLock(lockResult.data);
    if (logsResult.ok) setLogs(logsResult.data);
    if (serverResult.ok) setStatus(serverResult.data.status);
    await onSettledRef.current?.();
  }

  return {
    status,
    operation,
    logs,
    lock,
    events,
    logRef,
    running,
    busyAction,
    busyMirror,
    currentMirror,
    error,
    streamFailed,
    canForceStop,
    runAction,
    runS3Restore,
    onSwitchMirror,
    refreshAfterForceStop,
  };
}
