/**
 * Server status as reported by the backend. Kept as a string-literal union so
 * the UI can map each state to a status tone and label deterministically.
 */
export type ServerStatus =
  | "pending"
  | "deploying"
  | "running"
  | "stopped"
  | "error"
  | "unknown";

/**
 * Non-secret projection of a server used across list and card views. Secret
 * fields (SSH/RCON/GSLT/API keys) are intentionally excluded — the console
 * never receives them in summaries.
 */
export type ServerSummary = {
  readonly id: number;
  readonly name: string;
  readonly host: string;
  readonly gamePort: number;
  readonly status: ServerStatus;
  readonly description: string | null;
  readonly defaultMap: string;
  readonly maxPlayers: number;
};

export const SERVER_STATUS_META: Record<
  ServerStatus,
  { readonly label: string; readonly tone: "ok" | "warn" | "danger" | "info" | "neutral" }
> = {
  running: { label: "运行中", tone: "ok" },
  deploying: { label: "部署中", tone: "info" },
  pending: { label: "待部署", tone: "warn" },
  stopped: { label: "已停止", tone: "neutral" },
  error: { label: "异常", tone: "danger" },
  unknown: { label: "未知", tone: "neutral" },
};
