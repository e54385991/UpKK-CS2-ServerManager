"use client";

import { useEffect, useState } from "react";
import { fetchConsolePane } from "@/modules/console/pane-client";
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
    let cancelled = false;
    const pull = async () => {
      const next = await fetchConsolePane(serverId, kind);
      if (!cancelled && next) setPane(next);
    };
    void pull();
    const id = window.setInterval(() => void pull(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled, kind, serverId]);

  return pane;
}
