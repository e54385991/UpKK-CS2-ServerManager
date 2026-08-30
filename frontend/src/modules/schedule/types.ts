export const SCHEDULE_ACTIONS = [
  "start",
  "stop",
  "restart",
  "update",
  "validate",
  "backup_plugins",
] as const;

export const SCHEDULE_TYPES = ["daily", "weekly", "interval", "cron"] as const;

export type ScheduleAction = (typeof SCHEDULE_ACTIONS)[number];
export type ScheduleType = (typeof SCHEDULE_TYPES)[number];

export type ScheduledTask = {
  readonly id: number;
  readonly serverId: number;
  readonly name: string;
  readonly action: ScheduleAction;
  readonly enabled: boolean;
  readonly scheduleType: ScheduleType;
  readonly scheduleValue: string;
  readonly nextRun: string | null;
  readonly lastStatus: string | null;
};
