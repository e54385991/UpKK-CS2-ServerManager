"use client";

import { useEffect, useState } from "react";
import { subscribeConsolePanePoll } from "@/modules/console/pane-poll";
import type { ConsolePane, ConsolePaneKind } from "@/modules/console/types";

export function useConsolePane({
  serverId,
  kind,
  initial = null,
  enabled = true,
}: {
  serverId: number;
  kind: ConsolePaneKind;
  initial?: ConsolePane | null;
  enabled?: boolean;
}) {
  const [pane, setPane] = useState<ConsolePane | null>(initial);
  const snapshotKey = `${serverId}:${kind}`;
  const [seenKey, setSeenKey] = useState(snapshotKey);
  if (seenKey !== snapshotKey) {
    setSeenKey(snapshotKey);
    setPane(initial);
  }

  useEffect(() => {
    if (!enabled) return;
    return subscribeConsolePanePoll({
      serverId,
      kind,
      onPane: (next) => {
        if (next) setPane(next);
      },
    });
  }, [enabled, kind, serverId]);

  return pane;
}
