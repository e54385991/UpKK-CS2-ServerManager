"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  applyAptMirrorFromBrowser,
  loadCurrentOperationFromBrowser,
  loadOperationJournalFromBrowser,
  loadOperationSnapshotFromBrowser,
  restoreS3BackupFromBrowser,
  startServerOperationFromBrowser,
} from "@/modules/servers/operation-client";
import { toAptMirror, type AptMirrorId } from "@/modules/servers/apt-mirrors";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm } from "@/shared/feedback";
import {
  lastEventSequence,
  mergeOperationEvents,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";
import {
  requiresOperationConfirmation,
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
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";

export { isActiveOperation };

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
  const eventsRef = useRef(events);
  useEffect(() => {
    onSettledRef.current = onSettled;
  }, [onSettled]);
  useEffect(() => {
    operationRef.current = operation;
  }, [operation]);
  useEffect(() => {
    eventsRef.current = events;
  }, [events]);

  const running =
    isActiveOperation(operation) || busyAction !== null || busyMirror !== null;
  const operationId = operation?.operationId ?? null;
  const canForceStop =
    isActiveOperation(operation) || lock.lockActive || status === "deploying";

  const applySnapshot = useCallback(async () => {
    const snapshot = await loadOperationSnapshotFromBrowser(serverId);
    if (snapshot.ok) {
      setStatus(snapshot.data.server.status);
      setCurrentMirror(toAptMirror(snapshot.data.server.aptMirror));
      setLogs([...snapshot.data.logs]);
      setLock(snapshot.data.lock);
    }
    await onSettledRef.current?.();
  }, [serverId]);

  const refreshAfterTerminal = useCallback(async () => {
    await applySnapshot();
  }, [applySnapshot]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  useEffect(() => {
    if (!operationId) return;
    let cancelled = false;
    const pull = async () => {
      const result = await loadOperationJournalFromBrowser(serverId, operationId);
      if (cancelled || !result.ok) return;
      if (result.data.operation.operationId !== operationId) return;
      setOperation(result.data.operation);
      setEvents((current) => mergeOperationEvents(current, result.data.events));
      if (result.data.events.length > 0) setStreamFailed(false);
    };
    void pull();
    if (!isActiveOperation(operationRef.current)) {
      return () => {
        cancelled = true;
      };
    }
    const id = window.setInterval(() => {
      if (!isActiveOperation(operationRef.current)) return;
      void pull();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [operationId, serverId]);

  useEffect(() => {
    if (!operationId) return;
    let cancelled = false;
    let received = false;
    let finished = !isActiveOperation(operationRef.current);
    const seen = new Set<string>();
    const after = { current: lastEventSequence(eventsRef.current) };

    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event || (event.sequence && seen.has(event.sequence))) return null;
      if (event.sequence) seen.add(event.sequence);
      received = true;
      if (event.sequence && event.sequence !== "seed") {
        after.current = event.sequence;
      }
      setEvents((current) => mergeOperationEvents(current, [event]));
      return event;
    };

    const stop = subscribeVisibleEventSource({
      url: () => operationEventsUrl(serverId, operationId, after.current),
      eventTypes: ["progress", "operation_completed", "operation_failed"],
      shouldReconnect: () => !finished,
      onOpen: () => setStreamFailed(false),
      onUnavailable: () => {
        if (!received) setStreamFailed(true);
      },
      onData: (raw) => {
        const event = ingest(raw);
        if (
          event &&
          (event.type === "operation_completed" || event.type === "operation_failed")
        ) {
          const wasActive = !finished;
          finished = true;
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
          if (wasActive) void refreshAfterTerminal();
        }
      },
    });

    return () => {
      cancelled = true;
      stop();
    };
  }, [operationId, refreshAfterTerminal, serverId]);

  useEffect(() => {
    if (operationId) return;
    if (status !== "deploying" && !lock.lockActive) return;
    let cancelled = false;
    const tick = async () => {
      const result = await loadCurrentOperationFromBrowser(serverId);
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

  async function runAction(
    action: ServerOperationAction,
    options?: { readonly clearExecstack?: boolean },
  ) {
    if (running) return;
    if (
      requiresOperationConfirmation(action) &&
      !(await confirm(t(`confirm.${action}`)))
    ) {
      return;
    }
    setError(null);
    setStreamFailed(false);
    setBusyAction(action);
    const result = await startServerOperationFromBrowser(serverId, action, options);
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
    const result = await restoreS3BackupFromBrowser(serverId, objectKey);
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
    const result = await applyAptMirrorFromBrowser(serverId, mirror);
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
    const current = await loadCurrentOperationFromBrowser(serverId);
    if (current.ok) setOperation(current.data);
    await applySnapshot();
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
