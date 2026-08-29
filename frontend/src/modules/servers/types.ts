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

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/**
 * Status → visual tone. Human labels are resolved via i18n at render time
 * (`servers.status.<status>`), so this map holds presentation only.
 */
export const SERVER_STATUS_TONE: Record<ServerStatus, Tone> = {
  running: "ok",
  deploying: "info",
  pending: "warn",
  stopped: "neutral",
  error: "danger",
  unknown: "neutral",
};
