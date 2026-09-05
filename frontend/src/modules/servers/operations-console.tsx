"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import {
  CirclePlay,
  CircleStop,
  LoaderCircle,
  Rocket,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";
import { ForceStopButton } from "@/modules/servers/force-stop-button";
import {
  probeServerCompatibilityFromBrowser,
  updateExecstackPreferenceFromBrowser,
} from "@/modules/servers/operation-client";
import { AptMirrorSwitcher } from "@/modules/servers/apt-mirror-switcher";
import { OperationLiveLog, formatOperationClock } from "@/modules/servers/operation-live-log";
import { useOperationRunner } from "@/modules/servers/use-operation-runner";
import {
  isServerOperationAction,
  SERVER_STATUS_TONE,
  type DeploymentLock,
  type DeploymentLogEntry,
  type ServerOperation,
  type ServerOperationAction,
  type ServerStatus,
} from "@/modules/servers/types";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

const PRIMARY_ACTIONS: readonly ServerOperationAction[] = [
  "start",
  "stop",
  "deploy",
];
const MORE_ACTIONS: readonly ServerOperationAction[] = [
  "restart",
  "status",
  "update",
  "validate",
];
const DEFAULT_EXECSTACK_TARGET = "counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so";

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function OperationsConsole({
  serverId,
  serverStatus,
  initialOperation,
  initialLogs,
  initialLock,
  aptMirror,
  gameDirectory,
  clearExecstackEffective,
  clearExecstackOverride,
  execstackFixOnRestart,
  execstackFixTargets,
  osId,
  osVersion,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
  aptMirror: string | null;
  gameDirectory: string;
  clearExecstackEffective: boolean;
  clearExecstackOverride: boolean | null;
  execstackFixOnRestart: boolean;
  execstackFixTargets: readonly string[];
  osId: string | null;
  osVersion: string | null;
}) {
  const t = useTranslations("serverDetail");
  const tServers = useTranslations("servers");
  const format = useFormatter();
  const {
    status,
    operation,
    logs,
    events,
    logRef,
    running,
    busyAction,
    busyMirror,
    currentMirror,
    error,
    streamFailed,
    canForceStop,
    runAction,
    onSwitchMirror,
    refreshAfterForceStop,
  } = useOperationRunner({
    serverId,
    serverStatus,
    initialOperation,
    initialLogs,
    initialLock,
    aptMirror,
  });

  const statusTone = SERVER_STATUS_TONE[status];
  const emptyHint = useMemo(() => t("streamEmpty"), [t]);
  const displayedCommand = (execstackFixTargets.length > 0 ? execstackFixTargets : [DEFAULT_EXECSTACK_TARGET])
    .map((target) => `patchelf --clear-execstack ${shellQuote(`${gameDirectory}/cs2/game/csgo/addons/${target}`)}`)
    .join("\n");
  const [execstack, setExecstack] = useState(clearExecstackEffective);
  const [detectedOsId, setDetectedOsId] = useState(osId);
  const [detectedOsVersion, setDetectedOsVersion] = useState(osVersion);
  const [execstackSaving, setExecstackSaving] = useState(false);
  const [execstackError, setExecstackError] = useState<string | null>(null);
  const [execstackSource, setExecstackSource] = useState<"auto" | "manual">(
    clearExecstackOverride === null ? "auto" : "manual",
  );

  useEffect(() => {
    if (osId && osVersion) return;
    let cancelled = false;
    void probeServerCompatibilityFromBrowser(serverId).then((result) => {
      if (cancelled || !result.ok) return;
      const value = result.data.clear_execstack_effective;
      if (typeof value === "boolean") setExecstack(value);
      setDetectedOsId(result.data.os_id ?? null);
      setDetectedOsVersion(result.data.os_version ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [osId, osVersion, serverId]);

  async function saveExecstack(next: boolean) {
    setExecstack(next);
    setExecstackSaving(true);
    setExecstackError(null);
    const result = await updateExecstackPreferenceFromBrowser(serverId, next);
    setExecstackSaving(false);
    if (!result.ok) {
      setExecstack(clearExecstackEffective);
      setExecstackError(result.error);
      return;
    }
    setExecstackSource("manual");
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("operations")}</CardTitle>
            <CardDescription>{t("operationsHelp")}</CardDescription>
          </div>
          <Badge tone={statusTone}>
            <StatusDot tone={statusTone} pulse={status === "running"} />
            {tServers(`status.${status}`)}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-5">
          {canForceStop ? (
            <div className="flex items-start gap-3 rounded-md border border-danger/30 bg-danger-muted/40 px-3 py-3 text-sm text-danger">
              <ShieldAlert className="mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 flex-1 space-y-2">
                <p>{t("forceStopHelp")}</p>
                <ForceStopButton
                  serverId={serverId}
                  onDone={refreshAfterForceStop}
                />
              </div>
            </div>
          ) : null}

          <ActionGroup
            title={t("groups.primary")}
            actions={PRIMARY_ACTIONS}
            prominent
            running={running}
            busyAction={busyAction}
            onRun={(action) => {
              void runAction(
                action,
                action === "restart"
                  ? { clearExecstack: execstack && execstackFixOnRestart }
                  : undefined,
              );
            }}
            t={t}
          />
          <div className="space-y-2 rounded-md border border-line bg-surface-raised/40 px-3 py-3">
            <div className="flex items-start gap-3">
              <input
                id="clear-execstack"
                type="checkbox"
                className="mt-1 size-4 accent-primary"
                checked={execstack && execstackFixOnRestart}
                disabled={execstackSaving || running || !execstackFixOnRestart}
                onChange={(event) => void saveExecstack(event.target.checked)}
              />
              <div>
                <label htmlFor="clear-execstack" className="text-sm font-medium text-fg">
                  {t("clearExecstack")}
                </label>
                <p className="text-xs text-fg-muted">{t("clearExecstackHelp")}</p>
                <p className="text-xs text-fg-subtle">
                  {t("clearExecstackSource", { source: execstackSource })}
                </p>
                <p className="mt-2 text-xs text-fg-subtle">
                  {t("clearExecstackSystem", {
                    system: detectedOsId && detectedOsVersion
                      ? `${detectedOsId} ${detectedOsVersion}`
                      : t("clearExecstackUnknown"),
                  })}
                </p>
                <code className="mt-1 block break-all rounded bg-surface-raised px-2 py-1 text-[11px] text-fg">
                  {displayedCommand}
                </code>
                <Link
                  href={`/servers/${serverId}/additional-fixes`}
                  className="mt-2 inline-flex text-xs font-medium text-primary hover:text-primary-strong"
                >
                  {t("openAdditionalFixes")}
                </Link>
                {execstackError ? <p className="mt-2 text-xs text-danger">{execstackError}</p> : null}
              </div>
            </div>
          </div>
          <ActionGroup
            title={t("groups.more")}
            actions={MORE_ACTIONS}
            running={running}
            busyAction={busyAction}
            onRun={(action) => {
              void runAction(
                action,
                action === "restart"
                  ? { clearExecstack: execstack && execstackFixOnRestart }
                  : undefined,
              );
            }}
            t={t}
          />
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">
              {t("groups.aptMirrors")}
            </p>
            <p className="text-xs text-fg-muted">{t("aptMirrorHelp")}</p>
            <p className="text-sm text-fg">
              {currentMirror
                ? t("aptMirrorCurrent", { mirror: t(`mirrors.${currentMirror}`) })
                : t("aptMirrorUnset")}
            </p>
            <AptMirrorSwitcher
              current={currentMirror}
              disabled={running}
              busyMirror={busyMirror}
              onSelect={(mirror) => void onSwitchMirror(mirror)}
              labelFor={(mirror) => t(`mirrors.${mirror}`)}
              applyLabel={t("aptMirrorApply")}
            />
          </div>

          {error ? (
            <p className="rounded-md border border-danger/30 bg-danger-muted/40 px-3 py-2 text-sm text-danger">
              {error}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <div className="space-y-6">
        <OperationLiveLog
          serverId={serverId}
          operation={operation}
          events={events}
          logRef={logRef}
          streamFailed={streamFailed}
          canForceStop={canForceStop}
          emptyHint={emptyHint}
          onForceStopDone={refreshAfterForceStop}
        />

        <Card>
          <CardHeader>
            <CardTitle>{t("history")}</CardTitle>
            <CardDescription>{t("historyHelp")}</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {logs.length === 0 ? (
              <p className="px-5 py-8 text-sm text-fg-subtle">{t("historyEmpty")}</p>
            ) : (
              <ul className="divide-y divide-line">
                {logs.map((entry) => (
                  <li key={entry.id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium text-fg">
                        {isServerOperationAction(entry.action)
                          ? t(`actions.${entry.action}`)
                          : entry.action}
                      </p>
                      <span className="font-mono text-xs text-fg-subtle">
                        {entry.createdAt
                          ? formatOperationClock(entry.createdAt, format.dateTime)
                          : "—"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-fg-muted">
                      {entry.status}
                      {entry.errorMessage ? ` · ${entry.errorMessage}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
      </div>
    </div>
  );
}

function actionVariant(
  action: ServerOperationAction,
  prominent: boolean,
): "primary" | "secondary" | "outline" {
  if (action === "start") return "primary";
  if (action === "stop") return "outline";
  if (action === "deploy") return prominent ? "primary" : "outline";
  return "secondary";
}

function ActionIcon({ action }: { action: ServerOperationAction }) {
  if (action === "start") return <CirclePlay />;
  if (action === "stop") return <CircleStop />;
  if (action === "restart") return <RotateCcw />;
  if (action === "deploy") return <Rocket />;
  return null;
}

function ActionGroup({
  title,
  actions,
  prominent = false,
  running,
  busyAction,
  onRun,
  t,
}: {
  title: string;
  actions: readonly ServerOperationAction[];
  prominent?: boolean;
  running: boolean;
  busyAction: ServerOperationAction | null;
  onRun: (action: ServerOperationAction) => void;
  t: ReturnType<typeof useTranslations>;
}) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">
        {title}
      </p>
      <div className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button
            key={action}
            type="button"
            size={prominent ? "md" : "sm"}
            variant={actionVariant(action, prominent)}
            disabled={running}
            data-testid={`operations-action-${action}`}
            onClick={() => onRun(action)}
          >
            {busyAction === action ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <ActionIcon action={action} />
            )}
            {t(`actions.${action}`)}
          </Button>
        ))}
      </div>
    </div>
  );
}
