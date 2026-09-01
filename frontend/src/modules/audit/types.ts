export type AuditEntry = {
  readonly id: string;
  readonly createdAt: string | null;
  readonly category: string;
  readonly action: string;
  readonly status: string;
  readonly actorUsername: string | null;
  readonly ipAddress: string | null;
  readonly source: string;
  readonly serverId: number | null;
  readonly details: Record<string, unknown>;
};

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/** Filter option values. Human labels are resolved via i18n at render time. */
export const AUDIT_CATEGORY_VALUES = [
  "auth",
  "discord",
  "server",
  "settings",
  "files",
  "config",
  "plugin",
] as const;

export const AUDIT_STATUS_VALUES = [
  "success",
  "failure",
  "cancelled",
  "expired",
  "requested",
  "partial",
] as const;

/** Status → visual tone (presentation only). */
export const AUDIT_STATUS_TONE: Record<string, Tone> = {
  success: "ok",
  failure: "danger",
  cancelled: "neutral",
  expired: "warn",
  requested: "info",
  partial: "warn",
};

export function statusTone(value: string): Tone {
  return AUDIT_STATUS_TONE[value] ?? "neutral";
}
