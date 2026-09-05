"use client";

import { useTranslations } from "next-intl";
import { Network, ScrollText, TriangleAlert } from "lucide-react";
import {
  CLIENT_IP_HEADER_PRESETS,
  LOG_LEVELS,
  isLogLevel,
  type LogLevel,
  type SystemSettings,
} from "@/modules/settings/types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

// Sentinel choices that are not header names: use the socket peer, or type one.
export const DIRECT_CLIENT_IP = "__direct__";
export const CUSTOM_CLIENT_IP = "__custom__";

// Empty value = follow the backend's LOG_LEVEL environment variable.
export const ENVIRONMENT_LOG_LEVEL = "";

export function clientIpChoiceOf(header: string | null): string {
  if (!header) return DIRECT_CLIENT_IP;
  return (CLIENT_IP_HEADER_PRESETS as readonly string[]).includes(header)
    ? header
    : CUSTOM_CLIENT_IP;
}

export function customClientIpOf(header: string | null): string {
  return clientIpChoiceOf(header) === CUSTOM_CLIENT_IP ? (header ?? "") : "";
}

export function clientIpHeaderOf(choice: string, custom: string): string | null {
  if (choice === DIRECT_CLIENT_IP) return null;
  if (choice === CUSTOM_CLIENT_IP) return custom.trim() || null;
  return choice;
}

export function logLevelOf(choice: string): LogLevel | null {
  return isLogLevel(choice) ? choice : null;
}

/** Where the panel reads the visitor address it records and rate-limits by. */
export function ClientIpCard({
  settings,
  choice,
  onChoiceChange,
  custom,
  onCustomChange,
}: {
  settings: SystemSettings;
  choice: string;
  onChoiceChange: (value: string) => void;
  custom: string;
  onCustomChange: (value: string) => void;
}) {
  const t = useTranslations("settings");
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-md bg-info-muted text-info ring-1 ring-info/30">
            <Network className="size-4" />
          </span>
          <div>
            <CardTitle>{t("clientIp.title")}</CardTitle>
            <CardDescription>{t("clientIp.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="client-ip-source">{t("clientIp.source")}</Label>
            <Select
              id="client-ip-source"
              value={choice}
              onChange={(event) => onChoiceChange(event.target.value)}
            >
              {CLIENT_IP_HEADER_PRESETS.map((header) => (
                <option key={header} value={header}>
                  {header}
                </option>
              ))}
              <option value={CUSTOM_CLIENT_IP}>{t("clientIp.custom")}</option>
              <option value={DIRECT_CLIENT_IP}>{t("clientIp.direct")}</option>
            </Select>
            <p className="mt-1.5 text-xs text-fg-subtle">
              {t("clientIp.sourceHelp")}
            </p>
          </div>
          {choice === CUSTOM_CLIENT_IP ? (
            <div>
              <Label htmlFor="client-ip-header">
                {t("clientIp.customLabel")}
              </Label>
              <Input
                id="client-ip-header"
                value={custom}
                onChange={(event) => onCustomChange(event.target.value)}
                placeholder="X-Forwarded-For"
              />
              <p className="mt-1.5 text-xs text-fg-subtle">
                {t("clientIp.customHelp")}
              </p>
            </div>
          ) : null}
        </div>
        <p className="text-sm text-fg-muted">
          {settings.clientIpHeader
            ? t("clientIp.active", { header: settings.clientIpHeader })
            : t("clientIp.directHelp")}
        </p>
        <p className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-muted/30 px-3 py-2 text-xs text-fg-muted">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" />
          <span>{t("clientIp.trustWarning")}</span>
        </p>
      </CardContent>
    </Card>
  );
}

/** How much the panel prints to its console. The log file is unaffected. */
export function LoggingCard({
  settings,
  level,
  onLevelChange,
}: {
  settings: SystemSettings;
  level: string;
  onLevelChange: (value: string) => void;
}) {
  const t = useTranslations("settings");
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-md bg-info-muted text-info ring-1 ring-info/30">
            <ScrollText className="size-4" />
          </span>
          <div>
            <CardTitle>{t("logging.title")}</CardTitle>
            <CardDescription>{t("logging.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="console-log-level">
              {t("logging.consoleLevel")}
            </Label>
            <Select
              id="console-log-level"
              value={level}
              onChange={(event) => onLevelChange(event.target.value)}
            >
              {LOG_LEVELS.map((name) => (
                <option key={name} value={name}>
                  {t(`logging.levels.${name}`)}
                </option>
              ))}
              <option value={ENVIRONMENT_LOG_LEVEL}>
                {t("logging.followEnvironment")}
              </option>
            </Select>
            <p className="mt-1.5 text-xs text-fg-subtle">
              {t("logging.consoleLevelHelp")}
            </p>
          </div>
        </div>
        <p className="text-sm text-fg-muted">
          {settings.logLevel
            ? t("logging.active", { level: settings.logLevel })
            : t("logging.followEnvironmentHelp", {
                level: settings.effectiveLogLevel,
              })}
        </p>
        <p className="text-xs text-fg-subtle">{t("logging.fileNote")}</p>
      </CardContent>
    </Card>
  );
}
