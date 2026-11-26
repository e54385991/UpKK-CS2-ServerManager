import type { ReactNode } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type { ServerSummary } from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";

export type SshHealthFields = Pick<
  ServerSummary,
  | "id"
  | "isSshDown"
  | "sshHealthStatus"
  | "consecutiveSshFailures"
  | "sshHealthFailureThreshold"
  | "sshHealthCheckIntervalHours"
  | "lastSshHealthCheck"
>;

export function sshHealthTone(
  status: string,
  isSshDown: boolean,
): "ok" | "warn" | "danger" | "neutral" {
  if (status === "completely_down" || isSshDown) return "danger";
  if (status === "degraded" || status === "down") return "warn";
  if (status === "healthy") return "ok";
  return "neutral";
}

const KNOWN_HEALTH = [
  "healthy",
  "degraded",
  "down",
  "completely_down",
  "unknown",
] as const;

type KnownHealth = (typeof KNOWN_HEALTH)[number];

function isKnownHealth(value: string): value is KnownHealth {
  return (KNOWN_HEALTH as readonly string[]).includes(value);
}

function parseLastCheck(value: string | null): Date | string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Date(parsed);
}

function statusLabel(
  t: (key: `statusValue.${KnownHealth}`) => string,
  status: string,
): string {
  return isKnownHealth(status)
    ? t(`statusValue.${status}`)
    : status;
}

export function SshHealthDetails({
  server,
  children,
}: {
  server: SshHealthFields;
  children?: ReactNode;
}) {
  const t = useTranslations("servers.sshHealth");
  const format = useFormatter();
  const tone = sshHealthTone(server.sshHealthStatus, server.isSshDown);
  const lastCheck = parseLastCheck(server.lastSshHealthCheck);
  const lastCheckLabel = lastCheck instanceof Date
    ? format.dateTime(lastCheck, { dateStyle: "medium", timeStyle: "medium" })
    : lastCheck ?? t("never");

  return (
    <div
      className="mt-3 space-y-1.5 rounded-md border border-line bg-surface-overlay/50 px-3 py-2"
      data-testid="ssh-health"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-fg">{t("title")}</p>
        <Badge tone={tone} data-testid="ssh-health-badge">
          {statusLabel(t, server.sshHealthStatus)}
        </Badge>
      </div>
      <ul className="space-y-0.5 text-xs text-fg-muted">
        <li>
          {t("status")}:{" "}
          <span className="font-medium text-fg">
            {statusLabel(t, server.sshHealthStatus)}
          </span>
        </li>
        {server.consecutiveSshFailures > 0 ? (
          <li>
            {t("failures")}:{" "}
            <span className="font-medium text-fg">
              {server.consecutiveSshFailures}/{server.sshHealthFailureThreshold}
            </span>
          </li>
        ) : null}
        <li>
          {t("lastCheck")}: {lastCheckLabel}
        </li>
      </ul>
      {children}
    </div>
  );
}
