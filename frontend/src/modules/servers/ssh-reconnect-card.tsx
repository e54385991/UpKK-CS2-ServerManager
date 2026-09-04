"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  CircleStop,
  Download,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { reconnectServerSshAction } from "@/modules/servers/actions";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { LiveConsolePopups } from "@/modules/console/open-live-terminal";
import { ForceStopButton } from "@/modules/servers/force-stop-button";
import { startServerOperationFromBrowser } from "@/modules/servers/operation-client";
import { SshHealthBlock, type SshHealthFields } from "@/modules/servers/ssh-health-block";
import {
  requiresOperationConfirmation,
  type ServerOperationAction,
} from "@/modules/servers/types";
import { confirm } from "@/shared/feedback";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";

const QUICK_ACTIONS = ["restart", "stop", "update"] as const;

export function SshReconnectCard({
  serverId,
  serverName,
  isSshDown,
  sshPooled,
  sshInUse,
  sshActiveLeases,
  sshIdleSeconds,
  canForceStop,
  showDeploy = false,
  health,
}: {
  serverId: number;
  serverName?: string;
  isSshDown: boolean;
  sshPooled: boolean;
  sshInUse: boolean;
  sshActiveLeases: number;
  sshIdleSeconds: number | null;
  canForceStop: boolean;
  showDeploy?: boolean;
  health?: SshHealthFields;
}) {
  const t = useTranslations("serverDetail");
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [busyAction, setBusyAction] = useState<ServerOperationAction | null>(
    null,
  );
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const actionInFlight = useRef(false);
  const submitting = pending || busyAction !== null;

  const connected = sshPooled && !isSshDown;
  const tone = isSshDown ? "danger" : connected ? "ok" : "neutral";
  const statusLabel = isSshDown
    ? t("sshMarkedDown")
    : connected
      ? t("sshConnected")
      : t("sshDisconnected");
  const poolLabel = sshPooled
    ? sshInUse
      ? t("sshPoolBusy", { leases: sshActiveLeases })
      : t("sshPoolIdle", {
          seconds: Math.max(0, Math.round(sshIdleSeconds ?? 0)),
        })
    : t("sshPoolNone");

  async function onReconnect() {
    setPending(true);
    setMessage(null);
    setFailed(false);
    const result = await reconnectServerSshAction(serverId);
    setPending(false);
    if (!result.ok) {
      setFailed(true);
      setMessage(result.error || t("sshReconnectFail"));
      return;
    }
    setFailed(!result.data.success);
    setMessage(result.data.message || (result.data.success ? t("sshReconnectOk") : t("sshReconnectFail")));
    router.refresh();
  }

  async function onQuickAction(action: (typeof QUICK_ACTIONS)[number]) {
    if (submitting || actionInFlight.current) return;
    if (
      requiresOperationConfirmation(action) &&
      !(await confirm(t(`confirm.${action}`)))
    ) {
      return;
    }
    actionInFlight.current = true;
    setBusyAction(action);
    setMessage(null);
    setFailed(false);
    try {
      const result = await startServerOperationFromBrowser(serverId, action);
      if (!result.ok) {
        setFailed(true);
        setMessage(result.error || t("actionSubmitFail"));
        return;
      }
      trackQueuedOperation(result.data, {
        serverName: serverName ?? `#${serverId}`,
      });
      setMessage(t("actionQueued", { action: t(`actions.${action}`) }));
      router.refresh();
    } finally {
      actionInFlight.current = false;
      setBusyAction(null);
    }
  }

  return (
    <Card
      data-testid="workspace-ssh-card"
      className={
        isSshDown
          ? "mb-4 border-danger/30 bg-danger-muted/30 px-5 py-4"
          : "mb-4 px-5 py-4"
      }
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-fg">{t("sshStatusTitle")}</p>
            <Badge tone={tone}>
              <StatusDot tone={tone} pulse={connected} />
              {statusLabel}
            </Badge>
          </div>
          {isSshDown ? (
            <p className="max-w-2xl text-sm text-danger">{t("sshDownBanner")}</p>
          ) : (
            <p className="text-xs text-fg-subtle">{poolLabel}</p>
          )}
          {health ? <SshHealthBlock server={health} showReconnect={false} /> : null}
          {message ? (
            <p
              className={failed ? "text-sm text-danger" : "text-sm text-ok"}
              role="status"
            >
              {message}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {QUICK_ACTIONS.map((action) => (
            <Button
              key={action}
              type="button"
              size="sm"
              variant={action === "stop" ? "outline" : "secondary"}
              disabled={submitting}
              data-testid={`workspace-action-${action}`}
              onClick={() => void onQuickAction(action)}
            >
              {busyAction === action ? (
                <LoaderCircle className="animate-spin" />
              ) : action === "restart" ? (
                <RotateCcw />
              ) : action === "stop" ? (
                <CircleStop />
              ) : (
                <Download />
              )}
              {t(`actions.${action}`)}
            </Button>
          ))}
          <LiveConsolePopups serverId={serverId} showDeploy={showDeploy} />
          {canForceStop ? <ForceStopButton serverId={serverId} /> : null}
          <Button
            type="button"
            size="sm"
            variant={isSshDown ? "primary" : "outline"}
            disabled={submitting}
            onClick={() => void onReconnect()}
          >
            {pending ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
            {pending ? t("sshReconnecting") : t("sshReconnect")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
