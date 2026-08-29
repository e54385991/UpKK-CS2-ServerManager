export const COMMAND_TARGETS = ["host", "game_process"] as const;

export type CommandTarget = (typeof COMMAND_TARGETS)[number];

export type CustomCommand = {
  readonly id: number;
  readonly serverId: number;
  readonly name: string;
  readonly target: CommandTarget;
  readonly commands: string;
};

export type CommandExecuteResult = {
  readonly success: boolean;
  readonly message: string;
  readonly log: string;
};
