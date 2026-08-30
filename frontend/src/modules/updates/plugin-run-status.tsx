"use client";

import { useTranslations } from "next-intl";
import { pluginUpdateProgressPercent } from "@/modules/updates/intervals";
import {
  formatStatusTime,
  pluginStatusTone,
} from "@/modules/updates/status";
import type { PluginUpdateStatus } from "@/modules/updates/types";
import { Badge } from "@/shared/ui/badge";

export function PluginRunStatus({ status }: { status: PluginUpdateStatus }) {
  const t = useTranslations("pluginUpdates");
  if (status.state === "idle") return null;
  const percent = pluginUpdateProgressPercent(
    status.current,
    status.total,
    status.state,
  );
  return (
    <div
      className="space-y-2 rounded-md border border-line bg-surface-raised p-3"
      data-testid="plugin-run-status"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-fg">{t("liveStatus")}</p>
          <Badge tone={pluginStatusTone(status.state)}>{status.phase}</Badge>
        </div>
        <p className="text-xs text-fg-subtle">
          {status.current}/{status.total}
        </p>
      </div>
      {status.message ? (
        <p className="text-sm text-fg-muted">{status.message}</p>
      ) : null}
      <div className="h-2 overflow-hidden rounded-full bg-line">
        <div
          className={`h-full bg-primary transition-[width] ${
            status.state === "running" ? "animate-pulse" : ""
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {status.logs.length > 0 ? (
        <div className="max-h-44 overflow-y-auto rounded-md border border-line bg-canvas p-2 font-mono text-[11px] leading-5 text-fg">
          {status.logs.map((entry, index) => (
            <div key={`${entry.time ?? "log"}-${index}`}>
              {entry.time ? (
                <span className="text-fg-subtle">
                  {formatStatusTime(entry.time)}{" "}
                </span>
              ) : null}
              <span>{entry.message}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
