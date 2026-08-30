export type ConsoleKind = "game" | "ssh";
export type ConsolePaneKind = "game" | "steamcmd";

export type ConsoleWorkspace = {
  readonly serverId: number;
  readonly host: string;
  readonly sessionManager: "screen" | "tmux";
  readonly sshOk: boolean;
  readonly sshError: string | null;
  readonly gameRunning: boolean;
  readonly steamcmdRunning: boolean;
  readonly message: string | null;
};

export type ConsolePane = {
  readonly serverId: number;
  readonly kind: ConsolePaneKind;
  readonly sessionName: string;
  readonly sessionManager: "screen" | "tmux" | null;
  readonly sshOk: boolean;
  readonly running: boolean;
  readonly text: string;
  readonly heartbeat: string | null;
  readonly message: string | null;
};
