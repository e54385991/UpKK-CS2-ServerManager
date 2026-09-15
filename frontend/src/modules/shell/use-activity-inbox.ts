"use client";

import { useEffect, useState } from "react";
import { loadOperationInboxFromBrowser } from "@/modules/servers/operation-client";
import {
  OPERATION_INBOX_EVENTS_URL,
  parseOperationInboxPayload,
} from "@/modules/servers/operation-inbox";
import type { OperationInbox } from "@/modules/servers/types";
import { OPERATION_INBOX_LOCK, subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";
import {
  refreshSharedVisiblePoll,
  snapshotIsFresh,
  subscribeSharedVisiblePoll,
} from "@/shared/lib/visible-poll";

export const INBOX_POLL_KEY = "activity-tray:inbox";
const inboxPollState = { remaining: 0, lastSseAt: 0 };

export function setInboxPollRemaining(remaining: number) {
  inboxPollState.remaining = remaining;
}

export function useActivityInbox() {
  const [inbox, setInbox] = useState<OperationInbox | null>(null);

  useEffect(() => {
    let cancelled = false;
    const intervalMs = () => (inboxPollState.remaining > 0 ? 8_000 : 20_000);
    const stopPoll = subscribeSharedVisiblePoll({
      key: INBOX_POLL_KEY,
      intervalMs,
      shouldPull: () => !snapshotIsFresh(inboxPollState.lastSseAt, Date.now(), intervalMs()),
      pull: (signal) => loadOperationInboxFromBrowser({ signal }),
      onResult: (result) => {
        if (!cancelled && result.ok) setInbox(result.data);
      },
    });
    const onImport = () => {
      refreshSharedVisiblePoll(INBOX_POLL_KEY);
    };
    window.addEventListener("plugin-ai-import-submitted", onImport);
    const stop = subscribeVisibleEventSource({
      url: OPERATION_INBOX_EVENTS_URL,
      eventTypes: ["inbox"],
      lockName: OPERATION_INBOX_LOCK,
      onData: (raw) => {
        const next = parseOperationInboxPayload(raw);
        if (!cancelled && next) {
          inboxPollState.lastSseAt = Date.now();
          setInbox(next);
        }
      },
    });
    return () => {
      cancelled = true;
      window.removeEventListener("plugin-ai-import-submitted", onImport);
      stopPoll();
      stop();
    };
  }, []);

  return { inbox, setInbox };
}
