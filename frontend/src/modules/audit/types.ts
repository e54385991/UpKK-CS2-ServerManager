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
};

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

export const AUDIT_CATEGORIES = [
  { value: "auth", label: "认证" },
  { value: "discord", label: "Discord" },
  { value: "server", label: "服务器" },
  { value: "settings", label: "设置" },
] as const;

export const AUDIT_STATUSES = [
  { value: "success", label: "成功", tone: "ok" as Tone },
  { value: "failure", label: "失败", tone: "danger" as Tone },
  { value: "cancelled", label: "已取消", tone: "neutral" as Tone },
  { value: "expired", label: "已过期", tone: "warn" as Tone },
  { value: "requested", label: "已请求", tone: "info" as Tone },
] as const;

const CATEGORY_LABELS = new Map<string, string>(
  AUDIT_CATEGORIES.map((c) => [c.value, c.label]),
);
const STATUS_META = new Map<string, { label: string; tone: Tone }>(
  AUDIT_STATUSES.map((s) => [s.value, { label: s.label, tone: s.tone }]),
);

export function categoryLabel(value: string): string {
  return CATEGORY_LABELS.get(value) ?? value;
}

export function statusMeta(value: string): { label: string; tone: Tone } {
  return STATUS_META.get(value) ?? { label: value, tone: "neutral" };
}
