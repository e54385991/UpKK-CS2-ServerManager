export type ConsoleKind = "game" | "ssh";

export type ConsoleWorkspace = {
  readonly serverId: number;
  readonly host: string;
  readonly sessionManager: "screen" | "tmux";
  readonly sshOk: boolean;
  readonly sshError: string | null;
  readonly gameRunning: boolean;
  readonly message: string | null;
};
