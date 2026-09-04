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

export type AuditCategory = (typeof AUDIT_CATEGORY_VALUES)[number];

export function isAuditCategory(value: string): value is AuditCategory {
  return (AUDIT_CATEGORY_VALUES as readonly string[]).includes(value);
}

export const AUDIT_STATUS_VALUES = [
  "success",
  "failure",
  "cancelled",
  "expired",
  "requested",
  "partial",
] as const;

export type AuditStatus = (typeof AUDIT_STATUS_VALUES)[number];

export function isAuditStatus(value: string): value is AuditStatus {
  return (AUDIT_STATUS_VALUES as readonly string[]).includes(value);
}

/** Action paths present under the audit.actions message namespace. */
export const AUDIT_ACTION_VALUES = [
  "files.edit",
  "files.upload",
  "files.delete",
  "files.mkdir",
  "files.rename",
  "files.copy",
  "files.extract",
  "files.download_url",
  "files.cleanup",
  "files.cleanup_system",
  "config.plugin_file.update",
  "config.schedule.create",
  "config.schedule.update",
  "config.schedule.delete",
  "config.schedule.toggle",
  "config.plugin_updates",
  "config.game_updates",
  "config.cleanup.policy",
  "config.maps.add",
  "config.maps.enable",
  "config.maps.delete",
  "config.maps.preset",
  "config.maps.plugin_uninstall",
  "config.maps.plugin_config",
  "config.maps.custom_sync",
  "config.maps.custom_sync_run",
  "plugin.install",
  "plugin.uninstall",
  "plugin.catalog.delete",
  "plugin.catalog.import",
  "plugin.auto_update.run",
  "plugin.auto_update.test",
  "plugin.diagnostic.execute",
  "plugin.diagnostic.restore",
  "plugin.diagnostic.resume",
] as const;

export type AuditAction = (typeof AUDIT_ACTION_VALUES)[number];

export function isAuditAction(value: string): value is AuditAction {
  return (AUDIT_ACTION_VALUES as readonly string[]).includes(value);
}

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
