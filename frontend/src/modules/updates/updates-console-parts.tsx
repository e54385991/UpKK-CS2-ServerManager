"use client";

import { trackQueuedOperation } from "@/modules/servers/activity-store";
import {
  savePluginExcludesAction,
  testManagedPluginUpdateAction,
  togglePluginBackupAction,
  togglePluginRestartAction,
  unregisterManagedPluginAction
} from "@/modules/updates/actions";
import type {
  ManagedUpdatePlugin
} from "@/modules/updates/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

export function joinLines(values: readonly string[]): string {
  return values.join("\n");
}

export function splitLines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export type DateTimeFormatter = ReturnType<typeof useFormatter>["dateTime"];

export function formatWhen(
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

export function sourceLabel(
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

export function PluginUpdateSwitch({
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

export function PluginExcludeEditor({
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

