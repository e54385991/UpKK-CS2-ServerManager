import { fetchConsolePane } from "@/modules/console/pane-client";
import type { ConsolePane, ConsolePaneKind } from "@/modules/console/types";
import {
  subscribeSharedVisiblePoll,
  type VisiblePollHost,
} from "@/shared/lib/visible-poll";

export function panePollKey(serverId: number, kind: ConsolePaneKind): string {
  return `${serverId}:${kind}`;
}

export function subscribeConsolePanePoll(input: {
  serverId: number;
  kind: ConsolePaneKind;
  onPane: (pane: ConsolePane | null) => void;
  intervalMs?: number;
  fetchPane?: (
    serverId: number,
    kind: ConsolePaneKind,
    signal: AbortSignal,
  ) => Promise<ConsolePane | null>;
  host?: VisiblePollHost;
}): () => void {
  const fetchPane = input.fetchPane ?? fetchConsolePane;
  return subscribeSharedVisiblePoll({
    key: panePollKey(input.serverId, input.kind),
    intervalMs: input.intervalMs ?? 2_000,
    host: input.host,
    pull: (signal) => fetchPane(input.serverId, input.kind, signal),
    onResult: input.onPane,
  });
}
