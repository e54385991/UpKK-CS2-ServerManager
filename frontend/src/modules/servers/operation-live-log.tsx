"use client";

import type { RefObject } from "react";
import { useTranslations } from "next-intl";
import { LoaderCircle, SquareTerminal } from "lucide-react";
import { OpenLiveTerminalButton } from "@/modules/console/open-live-terminal";
import { ForceStopButton } from "@/modules/servers/force-stop-button";
import {
  OPERATION_STATUS_TONE,
  isActiveOperation,
  type OperationStreamEvent,
  type ServerOperation,
} from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { cn } from "@/shared/lib/cn";

function lineTone(kind: string): string {
  if (kind === "error") return "text-danger";
  if (kind === "complete") return "text-ok";
  if (kind === "status" || kind === "info") return "text-info";
  return "text-fg-muted";
}

export function formatOperationClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function OperationLiveLog({
  serverId,
  operation,
  events,
  logRef,
  streamFailed,
  canForceStop,
  emptyHint,
  description,
  onForceStopDone,
  showOpenLiveTerminal = true,
  className,
  logClassName,
}: {
  serverId: number;
  operation: ServerOperation | null;
  events: readonly OperationStreamEvent[];
  logRef: RefObject<HTMLDivElement | null>;
  streamFailed: boolean;
  canForceStop: boolean;
  emptyHint: string;
  description?: string;
  onForceStopDone: () => void | Promise<void>;
  showOpenLiveTerminal?: boolean;
  className?: string;
  logClassName?: string;
}) {
  const t = useTranslations("serverDetail");
  const displayEvents =
    events.length > 0
      ? events
      : operation
        ? [
            {
              sequence: "seed",
              operationId: operation.operationId,
              type: "progress",
              kind: "status",
              message: operation.message
                ? operation.message
                : isActiveOperation(operation)
                  ? t("streamConnecting")
                  : t("streamReplay", {
                      action: t(`actions.${operation.action}`),
                    }),
              timestamp: operation.startedAt,
            } satisfies OperationStreamEvent,
          ]
        : [];

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <SquareTerminal className="size-4 text-primary" />
          <div>
            <CardTitle>{t("streamTitle")}</CardTitle>
            <CardDescription>
              {description
                ? description
                : operation && isActiveOperation(operation)
                  ? t("streamFor", { action: t(`actions.${operation.action}`) })
                  : operation
                    ? t("streamReplay", {
                        action: t(`actions.${operation.action}`),
                      })
                    : t("streamIdle")}
            </CardDescription>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {showOpenLiveTerminal ? (
            <OpenLiveTerminalButton
              serverId={serverId}
              view={isActiveOperation(operation) ? "deploy" : undefined}
            />
          ) : null}
          {canForceStop ? (
            <ForceStopButton serverId={serverId} onDone={onForceStopDone} />
          ) : null}
          {operation ? (
            <Badge tone={OPERATION_STATUS_TONE[operation.status]}>
              {isActiveOperation(operation) ? (
                <LoaderCircle className="size-3 animate-spin" />
              ) : null}
              {t(`opStatus.${operation.status}`)}
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div
          ref={logRef}
          className={cn(
            "max-h-[28rem] overflow-auto bg-canvas px-4 py-3 font-mono text-xs leading-6",
            logClassName,
          )}
        >
          {displayEvents.length === 0 ? (
            <p className="text-fg-subtle">
              {streamFailed ? t("streamUnavailable") : emptyHint}
            </p>
          ) : (
            displayEvents.map((event) => (
              <p
                key={`${event.sequence}-${event.timestamp}-${event.message}`}
                className={cn("whitespace-pre-wrap break-all", lineTone(event.kind))}
              >
                <span className="mr-2 text-fg-subtle">
                  {formatOperationClock(event.timestamp)}
                </span>
                {event.message}
              </p>
            ))
          )}
        </div>
        {operation?.message && !isActiveOperation(operation) ? (
          <p className="border-t border-line px-4 py-3 text-sm text-fg-muted">
            {operation.message}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
