"use client";

import { useEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import {
  getPluginUpdateStatusAction,
  refreshPluginUpdatesAction,
  runPluginUpdatesAction,
  savePluginExcludesAction,
  savePluginUpdatesAction,
  testManagedPluginUpdateAction,
  togglePluginAutoUpdateAction,
  togglePluginBackupAction,
  togglePluginRestartAction,
  unregisterManagedPluginAction,
} from "@/modules/updates/actions";
import {
  PLUGIN_UPDATE_INTERVAL_MAX,
  PLUGIN_UPDATE_INTERVAL_MIN,
  clampPluginInterval,
} from "@/modules/updates/intervals";
import {
  addPostUpdateCommand,
  availablePostUpdateCommands,
  movePostUpdateCommand,
  removePostUpdateCommand,
} from "@/modules/updates/post-commands";
import { PluginRegisterForm } from "@/modules/updates/register-form";
import { PluginRunStatus } from "@/modules/updates/plugin-run-status";
import { pluginRunIsBusy } from "@/modules/updates/status";
import type { CustomCommand } from "@/modules/commands/types";
import type {
  ManagedUpdatePlugin,
  PluginUpdateStatus,
  PluginUpdates,
  RegisterMarketOption,
} from "@/modules/updates/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";

function joinLines(values: readonly string[]): string {
  return values.join("\n");
}

function splitLines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

type DateTimeFormatter = ReturnType<typeof useFormatter>["dateTime"];

function formatWhen(
  value: string | null,
  fallback: string,
  formatDateTime: DateTimeFormatter,
): string {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? fallback
    : formatDateTime(date, { dateStyle: "medium", timeStyle: "medium" });
}

function sourceLabel(
  sourceType: string,
  t: (key: "sourceTypes.github" | "sourceTypes.market" | "sourceTypes.framework") => string,
): string {
  if (
    sourceType === "github" ||
    sourceType === "market" ||
    sourceType === "framework"
  ) {
    return t(`sourceTypes.${sourceType}`);
  }
  return sourceType;
}

export function UpdatesConsole({
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

  return (
    <div className="space-y-6">
      {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="rounded-md border border-warn/30 bg-warn-muted/40 px-3 py-2 text-xs text-warn">
            {t("policyWarning")}
          </p>
          {runStatus ? <PluginRunStatus status={runStatus} /> : null}
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="auto-update">{t("enabled")}</Label>
            <Switch
              id="auto-update"
              label={t("enabled")}
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="interval">{t("interval")}</Label>
            <Input
              id="interval"
              type="number"
              min={PLUGIN_UPDATE_INTERVAL_MIN}
              max={PLUGIN_UPDATE_INTERVAL_MAX}
              step={0.1}
              value={intervalHours}
              onChange={(event) => setIntervalHours(event.target.value)}
            />
            <p className="text-xs text-fg-subtle">{t("intervalHelp")}</p>
          </div>
          <p className="text-xs text-fg-subtle">
            {t("lastCheck")}: {formatWhen(workspace.lastCheck, t("never"), format.dateTime)}
          </p>
          <div className="flex items-center justify-between gap-3">
            <div>
              <Label htmlFor="post-commands">{t("postCommands")}</Label>
              <p className="text-xs text-fg-subtle">{t("postCommandsHint")}</p>
            </div>
            <Switch
              id="post-commands"
              label={t("postCommands")}
              checked={postCommands}
              onCheckedChange={setPostCommands}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="post-command-add">{t("addCommand")}</Label>
            {savedCommands.length === 0 ? (
              <p className="text-xs text-fg-subtle">{t("noSavedCommands")}</p>
            ) : (
              <div className="flex flex-wrap items-end gap-2">
                <Select
                  id="post-command-add"
                  className="min-w-56 flex-1"
                  value={commandToAdd}
                  onChange={(event) => setCommandToAdd(event.target.value)}
                >
                  <option value="">{t("selectCommand")}</option>
                  {availableCommands.map((command) => (
                    <option key={command.id} value={String(command.id)}>
                      {command.name} ({tCommands(`targets.${command.target}`)})
                    </option>
                  ))}
                </Select>
                <Button
                  type="button"
                  variant="outline"
                  disabled={!commandToAdd}
                  onClick={() => {
                    const nextId = Number(commandToAdd);
                    if (!Number.isFinite(nextId)) return;
                    setCommandIds((current) => addPostUpdateCommand(current, nextId));
                    setCommandToAdd("");
                  }}
                >
                  {t("addCommand")}
                </Button>
              </div>
            )}
            {commandIds.length === 0 ? (
              <p className="text-xs text-fg-subtle">{t("noPostCommands")}</p>
            ) : (
              <ol className="space-y-2">
                {commandIds.map((commandId, index) => (
                  <li
                    key={`${commandId}-${index}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-surface-raised px-3 py-2"
                  >
                    <p className="text-sm text-fg">
                      <span className="mr-2 text-xs text-fg-subtle">{index + 1}</span>
                      {commandLabel(commandId)}
                    </p>
                    <div className="flex flex-wrap gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={index === 0}
                        onClick={() =>
                          setCommandIds((current) =>
                            movePostUpdateCommand(current, index, -1),
                          )
                        }
                      >
                        {t("moveUp")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={index === commandIds.length - 1}
                        onClick={() =>
                          setCommandIds((current) =>
                            movePostUpdateCommand(current, index, 1),
                          )
                        }
                      >
                        {t("moveDown")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setCommandIds((current) =>
                            removePostUpdateCommand(current, index),
                          )
                        }
                      >
                        {t("removeCommand")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={Boolean(pending)} onClick={() => void save()}>
              {pending === "save" ? t("saving") : t("save")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={Boolean(pending)}
              onClick={() => void run()}
            >
              {pending === "run" ? t("running") : t("run")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {workspace.plugins.length === 0 ? (
        <div className="space-y-3">
          <p className="text-sm text-fg-muted">{t("empty")}</p>
          <div
            className="grid gap-3 sm:grid-cols-2 rounded-lg border border-line bg-surface px-4 py-3"
            data-testid="plugin-exclude-fields"
          >
            <div className="space-y-1.5">
              <Label htmlFor="exclude-dirs-empty">{t("excludeDirs")}</Label>
              <Textarea
                id="exclude-dirs-empty"
                rows={3}
                disabled
                placeholder={t("excludeDirsHint")}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="exclude-files-empty">{t("excludeFiles")}</Label>
              <Textarea
                id="exclude-files-empty"
                rows={3}
                disabled
                placeholder={t("excludeFilesHint")}
              />
            </div>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled
            data-testid="plugin-unregister"
          >
            {t("unregister")}
          </Button>
        </div>
      ) : (
        <ul className="space-y-3">
          {workspace.plugins.map((plugin) => (
            <PluginExcludeEditor
              key={plugin.id}
              serverId={serverId}
              plugin={plugin}
              pending={pending}
              onPending={setPending}
              onBanner={setBanner}
              onSaved={replacePlugin}
              onKickStatus={() => setStatusEpoch((current) => current + 1)}
              onQueued={(operationId) => setQueuedOperationId(operationId)}
              onRemoved={(pluginId) =>
                setWorkspace((current) => ({
                  ...current,
                  plugins: current.plugins.filter((item) => item.id !== pluginId),
                }))
              }
              onToggle={(next) => void toggle(plugin.id, next)}
            />
          ))}
        </ul>
      )}

      <Card className="max-w-2xl">
        <CardContent className="pt-6">
          <PluginRegisterForm
            serverId={serverId}
            marketOptions={marketOptions}
            pending={pending}
            onPending={setPending}
            onBanner={setBanner}
            onRegistered={replacePlugin}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function PluginUpdateSwitch({
  id,
  label,
  description,
  checked,
  disabled,
  onCheckedChange,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (next: boolean) => void;
}) {
  const descriptionId = `${id}-description`;

  return (
    <div className="flex min-h-24 items-start justify-between gap-3 rounded-md border border-line bg-surface-overlay/40 px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{label}</p>
        <p
          id={descriptionId}
          className="mt-1 text-xs leading-5 text-fg-muted"
        >
          {description}
        </p>
      </div>
      <Switch
        id={id}
        label={label}
        description={description}
        descriptionId={descriptionId}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
      />
    </div>
  );
}

function PluginExcludeEditor({
  serverId,
  plugin,
  pending,
  onPending,
  onBanner,
  onSaved,
  onRemoved,
  onToggle,
  onKickStatus,
  onQueued,
}: {
  serverId: number;
  plugin: ManagedUpdatePlugin;
  pending: string | null;
  onPending: (value: string | null) => void;
  onBanner: (value: string | null) => void;
  onSaved: (plugin: ManagedUpdatePlugin) => void;
  onRemoved: (pluginId: number) => void;
  onToggle: (next: boolean) => void;
  onKickStatus: () => void;
  onQueued: (operationId: string) => void;
}) {
  const t = useTranslations("pluginUpdates");
  const format = useFormatter();
  const [dirs, setDirs] = useState(joinLines(plugin.excludeDirs));
  const [files, setFiles] = useState(joinLines(plugin.excludeFiles));

  async function saveExcludes() {
    onPending(`excludes-${plugin.id}`);
    onBanner(null);
    const result = await savePluginExcludesAction(serverId, plugin.id, {
      excludeDirs: splitLines(dirs),
      excludeFiles: splitLines(files),
    });
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    setDirs(joinLines(result.data.excludeDirs));
    setFiles(joinLines(result.data.excludeFiles));
    onSaved(result.data);
    onBanner(t("excludesSaved"));
  }

  async function toggleBackup(next: boolean) {
    onPending(`backup-${plugin.id}`);
    const result = await togglePluginBackupAction(serverId, plugin.id, next);
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    onSaved(result.data);
  }

  async function toggleRestart(next: boolean) {
    onPending(`restart-${plugin.id}`);
    const result = await togglePluginRestartAction(serverId, plugin.id, next);
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    onSaved(result.data);
  }

  async function testUpdate() {
    onPending(`test-${plugin.id}`);
    const result = await testManagedPluginUpdateAction(serverId, plugin.id);
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    trackQueuedOperation(result.data);
    onQueued(result.data.operationId);
    onBanner(t("queuedToTray"));
    onKickStatus();
  }

  async function unregister() {
    if (!(await confirm(t("unregisterConfirm", { name: plugin.displayName })))) {
      return;
    }
    onPending(`unregister-${plugin.id}`);
    onBanner(null);
    const result = await unregisterManagedPluginAction(serverId, plugin.id);
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    onRemoved(plugin.id);
    onBanner(result.data.message || t("unregistered"));
  }

  return (
    <li className="space-y-3 rounded-lg border border-line bg-surface px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium">{plugin.displayName}</p>
          <p className="text-xs text-fg-muted">
            {plugin.installedVersion} / {plugin.latestVersion ?? "—"}
            {" · "}
            {sourceLabel(plugin.sourceType, t)}
            {plugin.lastStatus ? ` · ${plugin.lastStatus}` : ""}
          </p>
          <p className="text-xs text-fg-subtle">
            {t("lastItemCheck")}: {formatWhen(plugin.lastCheckAt, t("never"), format.dateTime)}
          </p>
          {plugin.lastError ? (
            <p className="text-xs text-danger">{plugin.lastError}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {plugin.lastStatus ? <Badge>{plugin.lastStatus}</Badge> : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={Boolean(pending)}
            onClick={() => void testUpdate()}
          >
            {pending === `test-${plugin.id}` ? t("testing") : t("test")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="danger"
            disabled={Boolean(pending)}
            data-testid="plugin-unregister"
            onClick={() => void unregister()}
          >
            {pending === `unregister-${plugin.id}` ? t("unregistering") : t("unregister")}
          </Button>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-3" data-testid="plugin-update-settings">
        <PluginUpdateSwitch
          id={`plugin-${plugin.id}`}
          label={t("auto")}
          description={t("autoHint")}
          checked={plugin.autoUpdateEnabled}
          disabled={pending === `plugin-${plugin.id}`}
          onCheckedChange={onToggle}
        />
        <PluginUpdateSwitch
          id={`backup-${plugin.id}`}
          label={t("backup")}
          description={t("backupHint")}
          checked={plugin.backupBeforeUpdate}
          disabled={pending === `backup-${plugin.id}`}
          onCheckedChange={(next) => void toggleBackup(next)}
        />
        <PluginUpdateSwitch
          id={`restart-${plugin.id}`}
          label={t("restart")}
          description={t("restartHint")}
          checked={plugin.restartAfterUpdate}
          disabled={pending === `restart-${plugin.id}`}
          onCheckedChange={(next) => void toggleRestart(next)}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2" data-testid="plugin-exclude-fields">
        <div className="space-y-1.5">
          <Label htmlFor={`exclude-dirs-${plugin.id}`}>{t("excludeDirs")}</Label>
          <Textarea
            id={`exclude-dirs-${plugin.id}`}
            rows={3}
            value={dirs}
            placeholder={t("excludeDirsHint")}
            onChange={(event) => setDirs(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`exclude-files-${plugin.id}`}>{t("excludeFiles")}</Label>
          <Textarea
            id={`exclude-files-${plugin.id}`}
            rows={3}
            value={files}
            placeholder={t("excludeFilesHint")}
            onChange={(event) => setFiles(event.target.value)}
          />
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={Boolean(pending)}
        onClick={() => void saveExcludes()}
      >
        {pending === `excludes-${plugin.id}` ? t("saving") : t("saveExcludes")}
      </Button>
    </li>
  );
}
