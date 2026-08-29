"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { isDeployProgressVisible } from "@/modules/console/live-console";
import { openLiveTerminal } from "@/modules/console/open-live-terminal";
import { loadCurrentOperationFromBrowser } from "@/modules/servers/operation-client";
import {
  isActiveOperation,
  type ServerOperation,
} from "@/modules/servers/types";
import {
  refreshGameUpdatesAction,
  saveGameUpdatesAction,
  startGameUpdateAction,
} from "@/modules/updates/actions";
import type { GameUpdateAction, GameUpdates } from "@/modules/updates/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";

const INTERVALS = [
  { value: 0.0167, key: "1m" },
  { value: 0.0833, key: "5m" },
  { value: 0.25, key: "15m" },
  { value: 0.5, key: "30m" },
  { value: 1, key: "1h" },
  { value: 2, key: "2h" },
  { value: 6, key: "6h" },
  { value: 12, key: "12h" },
  { value: 24, key: "24h" },
] as const;

function formatWhen(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString();
}

function closestInterval(hours: number): number {
  let best: number = INTERVALS[0].value;
  let delta = Math.abs(hours - best);
  for (const item of INTERVALS) {
    const next = Math.abs(hours - item.value);
    if (next < delta) {
      best = item.value;
      delta = next;
    }
  }
  return best;
}

export function GameUpdatesConsole({
  serverId,
  initial,
  currentOperation,
}: {
  serverId: number;
  initial: GameUpdates;
  currentOperation: ServerOperation | null;
}) {
  const t = useTranslations("gameUpdates");
  const tActions = useTranslations("serverDetail.actions");
  const [workspace, setWorkspace] = useState(initial);
  const [enabled, setEnabled] = useState(initial.enableAutoUpdate);
  const [intervalHours, setIntervalHours] = useState(
    String(closestInterval(initial.intervalHours)),
  );
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [startedOp, setStartedOp] = useState<ServerOperation | null>(null);

  const statusTone =
    workspace.upToDate === true
      ? "ok"
      : workspace.upToDate === false
        ? "warn"
        : "neutral";
  const statusLabel =
    workspace.upToDate === true
      ? t("statusCurrent")
      : workspace.upToDate === false
        ? t("statusOutdated")
        : t("statusUnknown");

  const intervalOptions = useMemo(() => {
    const selected = Number(intervalHours);
    if (INTERVALS.some((item) => item.value === selected)) return INTERVALS;
    return [{ value: selected, key: "custom" as const }, ...INTERVALS];
  }, [intervalHours]);

  async function save() {
    setPending("save");
    setBanner(null);
    const parsed = Number(intervalHours);
    const result = await saveGameUpdatesAction(serverId, {
      enableAutoUpdate: enabled,
      intervalHours: Number.isFinite(parsed) ? parsed : workspace.intervalHours,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setWorkspace(result.data);
    setBanner(t("saved"));
  }

  async function refresh() {
    setPending("refresh");
    setBanner(null);
    const result = await refreshGameUpdatesAction(serverId, true);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setWorkspace(result.data);
    setEnabled(result.data.enableAutoUpdate);
    setIntervalHours(String(closestInterval(result.data.intervalHours)));
    setBanner(t("refreshed"));
  }

  async function start(action: GameUpdateAction) {
    const current = await loadCurrentOperationFromBrowser(serverId);
    const active = current.ok ? current.data : null;
    if (isActiveOperation(active) && active) {
      setBanner(t("busy", { action: active.action }));
      return;
    }
    const confirmKey = action === "update" ? "confirmUpdate" : "confirmValidate";
    if (!(await confirm(t(confirmKey)))) return;
    setPending(action);
    setBanner(null);
    const result = await startGameUpdateAction(serverId, action);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setStartedOp(result.data);
    setBanner(t("started", { action: tActions(action) }));
    openLiveTerminal(serverId, "deploy");
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <div>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </div>
        <Badge tone={statusTone}>{statusLabel}</Badge>
      </CardHeader>
      <CardContent className="space-y-5">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        {!workspace.steamCheckOk ? (
          <p className="text-sm text-warn">{t("steamUnreachable")}</p>
        ) : null}
        {workspace.steamError ? (
          <p className="text-sm text-warn">
            {t("steamError", { error: workspace.steamError })}
          </p>
        ) : null}

        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-fg-subtle">{t("installed")}</dt>
            <dd className="text-sm text-fg">
              {workspace.installedVersion ?? t("unknown")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-subtle">{t("advertised")}</dt>
            <dd className="text-sm text-fg">
              {workspace.advertisedVersion ?? t("unknown")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-subtle">{t("buildId")}</dt>
            <dd className="text-sm text-fg">
              {workspace.installedBuildId ?? t("unknown")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-subtle">{t("source")}</dt>
            <dd className="text-sm text-fg">
              {t(
                `sources.${workspace.installedSource === "steam.inf" ? "steamInf" : workspace.installedSource}`,
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-subtle">{t("lastCheck")}</dt>
            <dd className="text-sm text-fg">
              {formatWhen(workspace.lastUpdateCheck, t("never"))}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-subtle">{t("lastUpdate")}</dt>
            <dd className="text-sm text-fg">
              {formatWhen(workspace.lastUpdateTime, t("never"))}
            </dd>
          </div>
        </dl>

        {isActiveOperation(currentOperation) && currentOperation ? (
          <p className="text-sm text-warn">
            {t("busy", { action: currentOperation.action })}
          </p>
        ) : null}

        <div className="flex items-center justify-between gap-3">
          <div>
            <Label htmlFor="game-auto-update">{t("enabled")}</Label>
            <p className="text-xs text-fg-muted">{t("enabledHelp")}</p>
          </div>
          <Switch
            id="game-auto-update"
            label={t("enabled")}
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="game-interval">{t("interval")}</Label>
          <Select
            id="game-interval"
            value={intervalHours}
            onChange={(event) => setIntervalHours(event.target.value)}
          >
            {intervalOptions.map((item) => (
              <option key={item.key} value={String(item.value)}>
                {item.key === "custom"
                  ? `${item.value}`
                  : t(`intervals.${item.key}`)}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={Boolean(pending)} onClick={() => void save()}>
            {pending === "save" ? t("saving") : t("save")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(pending)}
            onClick={() => void refresh()}
          >
            {pending === "refresh" ? t("refreshing") : t("refresh")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(pending) || isActiveOperation(currentOperation)}
            onClick={() => void start("update")}
          >
            {pending === "update" ? t("updating") : t("update")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(pending) || isActiveOperation(currentOperation)}
            onClick={() => void start("validate")}
          >
            {pending === "validate" ? t("validating") : t("validate")}
          </Button>
          {isDeployProgressVisible({
            operation: startedOp ?? currentOperation,
          }) ? (
            <Button
              type="button"
              variant="ghost"
              data-testid="open-live-deploy"
              onClick={() => openLiveTerminal(serverId, "deploy")}
            >
              {t("watchLive")}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
