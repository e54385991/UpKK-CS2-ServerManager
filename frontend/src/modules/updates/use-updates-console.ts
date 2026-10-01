"use client";

import type { CustomCommand } from "@/modules/commands/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import {
  getPluginUpdateStatusAction,
  refreshPluginUpdatesAction,
  runPluginUpdatesAction,
  savePluginUpdatesAction,
  togglePluginAutoUpdateAction
} from "@/modules/updates/actions";
import {
  clampPluginInterval
} from "@/modules/updates/intervals";
import {
  availablePostUpdateCommands
} from "@/modules/updates/post-commands";
import { pluginRunIsBusy } from "@/modules/updates/status";
import type {
  ManagedUpdatePlugin,
  PluginUpdateStatus,
  PluginUpdates,
  RegisterMarketOption,
} from "@/modules/updates/types";
import { useFormatter, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

export function useUpdatesConsole({
  serverId,
  initial,
  savedCommands,
  marketOptions,
}: {
  serverId: number;
  initial: PluginUpdates;
  savedCommands: readonly CustomCommand[];
  marketOptions: readonly RegisterMarketOption[];
}) {
  const t = useTranslations("pluginUpdates");
  const tCommands = useTranslations("quickCommands");
  const format = useFormatter();
  const [workspace, setWorkspace] = useState(initial);
  const [enabled, setEnabled] = useState(initial.enableAutoUpdate);
  const [intervalHours, setIntervalHours] = useState(String(initial.intervalHours));
  const [postCommands, setPostCommands] = useState(initial.enablePostCommands);
  const [commandIds, setCommandIds] = useState<number[]>([...initial.commandIds]);
  const [commandToAdd, setCommandToAdd] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<PluginUpdateStatus | null>(null);
  const [statusEpoch, setStatusEpoch] = useState(0);
  const [queuedOperationId, setQueuedOperationId] = useState<string | null>(null);
  const seenFinishedAt = useRef<string | null | undefined>(undefined);
  const availableCommands = availablePostUpdateCommands(savedCommands, commandIds);

  function replacePlugin(next: ManagedUpdatePlugin) {
    setWorkspace((current) => ({
      ...current,
      plugins: current.plugins.some((item) => item.id === next.id)
        ? current.plugins.map((item) => (item.id === next.id ? next : item))
        : [...current.plugins, next],
    }));
  }

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick(keepFast: boolean) {
      const result = await getPluginUpdateStatusAction(serverId);
      if (cancelled) return;
      const running = result.ok && pluginRunIsBusy(result.data.state);
      if (result.ok) setRunStatus(result.data);
      timer = setTimeout(
        () => void tick(false),
        running || keepFast ? 1500 : 5000,
      );
    }

    void tick(statusEpoch > 0);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [serverId, statusEpoch]);

  useEffect(() => {
    const finishedAt = runStatus?.finishedAt ?? null;
    const state = runStatus?.state ?? "idle";
    if (seenFinishedAt.current === undefined) {
      seenFinishedAt.current = finishedAt;
      return;
    }
    if (
      finishedAt &&
      finishedAt !== seenFinishedAt.current &&
      (state === "completed" || state === "failed")
    ) {
      seenFinishedAt.current = finishedAt;
      void refreshPluginUpdatesAction(serverId).then((result) => {
        if (!result.ok) return;
        setWorkspace((current) => ({
          ...current,
          lastCheck: result.data.lastCheck,
          plugins: result.data.plugins,
        }));
      });
      return;
    }
    if (finishedAt) seenFinishedAt.current = finishedAt;
  }, [runStatus?.finishedAt, runStatus?.state, serverId]);

  useQueuedOperationTerminal(queuedOperationId, serverId, (status, message) => {
    setStatusEpoch((current) => current + 1);
    setBanner(message || (status === "failed" ? t("failed") : t("queuedDone")));
    void refreshPluginUpdatesAction(serverId).then((result) => {
      if (!result.ok) return;
      setWorkspace((current) => ({
        ...current,
        lastCheck: result.data.lastCheck,
        plugins: result.data.plugins,
      }));
    });
  });

  async function save() {
    setPending("save");
    setBanner(null);
    const parsed = Number(intervalHours);
    const result = await savePluginUpdatesAction(serverId, {
      enableAutoUpdate: enabled,
      intervalHours: clampPluginInterval(parsed, workspace.intervalHours),
      enablePostCommands: postCommands,
      commandIds,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setWorkspace(result.data);
    setEnabled(result.data.enableAutoUpdate);
    setIntervalHours(String(result.data.intervalHours));
    setPostCommands(result.data.enablePostCommands);
    setCommandIds([...result.data.commandIds]);
    setBanner(t("saved"));
  }

  async function run() {
    setPending("run");
    const result = await runPluginUpdatesAction(serverId);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    trackQueuedOperation(result.data);
    setQueuedOperationId(result.data.operationId);
    setBanner(t("queuedToTray"));
    setStatusEpoch((current) => current + 1);
  }

  async function toggle(pluginId: number, next: boolean) {
    setPending(`plugin-${pluginId}`);
    const result = await togglePluginAutoUpdateAction(serverId, pluginId, next);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    replacePlugin(result.data);
  }

  function commandLabel(commandId: number): string {
    const command = savedCommands.find((item) => item.id === commandId);
    if (!command) return t("missingCommand", { id: commandId });
    return `${command.name} (${tCommands(`targets.${command.target}`)})`;
  }

  return {
    t,
    tCommands,
    format,
    workspace,
    setWorkspace,
    enabled,
    setEnabled,
    intervalHours,
    setIntervalHours,
    postCommands,
    setPostCommands,
    commandIds,
    setCommandIds,
    commandToAdd,
    setCommandToAdd,
    pending,
    setPending,
    banner,
    setBanner,
    runStatus,
    setStatusEpoch,
    setQueuedOperationId,
    availableCommands,
    replacePlugin,
    save,
    run,
    toggle,
    commandLabel,
  };
}
