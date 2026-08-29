"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  runPluginUpdatesAction,
  savePluginExcludesAction,
  savePluginUpdatesAction,
  testManagedPluginUpdateAction,
  togglePluginAutoUpdateAction,
  togglePluginBackupAction,
  togglePluginRestartAction,
  unregisterManagedPluginAction,
} from "@/modules/updates/actions";
import { PluginRegisterForm } from "@/modules/updates/register-form";
import type { CustomCommand } from "@/modules/commands/types";
import type {
  ManagedUpdatePlugin,
  PluginUpdates,
  RegisterMarketOption,
} from "@/modules/updates/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
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
  const [workspace, setWorkspace] = useState(initial);
  const [enabled, setEnabled] = useState(initial.enableAutoUpdate);
  const [intervalHours, setIntervalHours] = useState(String(initial.intervalHours));
  const [postCommands, setPostCommands] = useState(initial.enablePostCommands);
  const [commandIds, setCommandIds] = useState<number[]>([...initial.commandIds]);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  function replacePlugin(next: ManagedUpdatePlugin) {
    setWorkspace((current) => ({
      ...current,
      plugins: current.plugins.some((item) => item.id === next.id)
        ? current.plugins.map((item) => (item.id === next.id ? next : item))
        : [...current.plugins, next],
    }));
  }

  async function save() {
    setPending("save");
    setBanner(null);
    const parsed = Number(intervalHours);
    const result = await savePluginUpdatesAction(serverId, {
      enableAutoUpdate: enabled,
      intervalHours: Number.isFinite(parsed) ? parsed : workspace.intervalHours,
      enablePostCommands: postCommands,
      commandIds,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setWorkspace(result.data);
    setBanner(t("saved"));
  }

  async function run() {
    setPending("run");
    const result = await runPluginUpdatesAction(serverId);
    setPending(null);
    setBanner(result.ok ? result.data.message : result.error || t("failed"));
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

  return (
    <div className="space-y-6">
      {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
              value={intervalHours}
              onChange={(event) => setIntervalHours(event.target.value)}
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="post-commands">{t("postCommands")}</Label>
            <Switch
              id="post-commands"
              label={t("postCommands")}
              checked={postCommands}
              onCheckedChange={setPostCommands}
            />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium text-fg-muted">{t("commandIds")}</p>
            {savedCommands.length === 0 ? (
              <p className="text-xs text-fg-subtle">{t("noSavedCommands")}</p>
            ) : (
              <ul className="space-y-2">
                {savedCommands.map((command) => (
                  <li key={command.id} className="flex items-center gap-2">
                    <input
                      id={`post-command-${command.id}`}
                      type="checkbox"
                      checked={commandIds.includes(command.id)}
                      onChange={(event) =>
                        setCommandIds((current) =>
                          event.target.checked
                            ? [...current, command.id]
                            : current.filter((id) => id !== command.id),
                        )
                      }
                    />
                    <Label htmlFor={`post-command-${command.id}`} className="mb-0">
                      {command.name}
                    </Label>
                  </li>
                ))}
              </ul>
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

function PluginExcludeEditor({
  serverId,
  plugin,
  pending,
  onPending,
  onBanner,
  onSaved,
  onRemoved,
  onToggle,
}: {
  serverId: number;
  plugin: ManagedUpdatePlugin;
  pending: string | null;
  onPending: (value: string | null) => void;
  onBanner: (value: string | null) => void;
  onSaved: (plugin: ManagedUpdatePlugin) => void;
  onRemoved: (pluginId: number) => void;
  onToggle: (next: boolean) => void;
}) {
  const t = useTranslations("pluginUpdates");
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
    onBanner(result.ok ? result.data.message : result.error || t("failed"));
  }

  async function unregister() {
    if (!window.confirm(t("unregisterConfirm", { name: plugin.displayName }))) {
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium">{plugin.displayName}</p>
          <p className="text-xs text-fg-muted">
            {t("version")}: {plugin.installedVersion} / {plugin.latestVersion ?? "—"}
          </p>
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
          <Switch
            id={`plugin-${plugin.id}`}
            label={t("auto")}
            checked={plugin.autoUpdateEnabled}
            disabled={pending === `plugin-${plugin.id}`}
            onCheckedChange={onToggle}
          />
          <Switch
            id={`backup-${plugin.id}`}
            label={t("backup")}
            checked={plugin.backupBeforeUpdate}
            disabled={pending === `backup-${plugin.id}`}
            onCheckedChange={(next) => void toggleBackup(next)}
          />
          <Switch
            id={`restart-${plugin.id}`}
            label={t("restart")}
            checked={plugin.restartAfterUpdate}
            disabled={pending === `restart-${plugin.id}`}
            onCheckedChange={(next) => void toggleRestart(next)}
          />
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
