"use client";

import type { CustomCommand } from "@/modules/commands/types";
import {
PLUGIN_UPDATE_INTERVAL_MAX,
PLUGIN_UPDATE_INTERVAL_MIN
} from "@/modules/updates/intervals";
import { PluginRunStatus } from "@/modules/updates/plugin-run-status";
import {
addPostUpdateCommand,
movePostUpdateCommand,
removePostUpdateCommand
} from "@/modules/updates/post-commands";
import { PluginRegisterForm } from "@/modules/updates/register-form";
import type {
PluginUpdates,
RegisterMarketOption
} from "@/modules/updates/types";
import { Button } from "@/shared/ui/button";
import {
Card,
CardContent,
CardDescription,
CardHeader,
CardTitle,
} from "@/shared/ui/card";
import { Input,Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";

import { PluginExcludeEditor, formatWhen } from "./updates-console-parts";
import { useUpdatesConsole } from "./use-updates-console";

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
  const {
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
  } = useUpdatesConsole({ serverId, initial, savedCommands, marketOptions });

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
