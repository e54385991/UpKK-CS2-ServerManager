"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Brush, HardDriveDownload, Save, Trash2 } from "lucide-react";
import {
  pluginDownloadCacheAction,
  refreshSettingsAction,
  saveSettingsAction,
} from "@/modules/settings/actions";
import type { SystemSettings } from "@/modules/settings/types";
import { confirm } from "@/shared/feedback";
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

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Reuse of downloaded plugin and framework archives, plus its cleanup policy.
 *
 * This card saves on its own rather than joining the main settings form: the
 * clear and prune buttons act on the directory immediately, and pairing them
 * with an unsaved path in the form would apply them to the wrong directory.
 */
export function DownloadCacheCard({ initial, onDirty, onSaved }: { initial: SystemSettings; onDirty?: () => void; onSaved?: () => void }) {
  const t = useTranslations("settings.downloadCache");
  const [settings, setSettings] = useState(initial);
  const [enabled, setEnabled] = useState(initial.pluginDownloadCacheEnabled);
  const [path, setPath] = useState(initial.pluginDownloadCachePath ?? "");
  const [maxAgeDays, setMaxAgeDays] = useState(
    String(initial.pluginDownloadCacheMaxAgeDays),
  );
  const [maxMegabytes, setMaxMegabytes] = useState(
    String(initial.pluginDownloadCacheMaxMegabytes),
  );
  const [busy, setBusy] = useState<"save" | "prune" | "clear" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function boundedNumber(value: string, max: number): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return 0;
    return Math.max(0, Math.min(max, Math.trunc(parsed)));
  }

  async function onSave() {
    setBusy("save");
    setMessage(null);
    const result = await saveSettingsAction({
      pluginDownloadCacheEnabled: enabled,
      pluginDownloadCachePath: path.trim() || null,
      pluginDownloadCacheMaxAgeDays: boundedNumber(maxAgeDays, 3650),
      pluginDownloadCacheMaxMegabytes: boundedNumber(maxMegabytes, 1_048_576),
    });
    setBusy(null);
    if (!result.ok) {
      setMessage(result.error || t("saveFailed"));
      return;
    }
    setSettings(result.data);
    setMaxAgeDays(String(result.data.pluginDownloadCacheMaxAgeDays));
    setMaxMegabytes(String(result.data.pluginDownloadCacheMaxMegabytes));
    onSaved?.();
    setMessage(t("saved"));
  }

  async function onMaintenance(action: "clear" | "prune") {
    if (action === "clear" && !(await confirm(t("clearConfirm")))) return;
    setBusy(action);
    setMessage(null);
    const result = await pluginDownloadCacheAction(action);
    setBusy(null);
    setMessage(result.ok ? result.data.message : result.error || t("saveFailed"));
    const refreshed = await refreshSettingsAction();
    if (refreshed.ok) setSettings(refreshed.data);
  }

  return (
    <Card onChange={onDirty}>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
            <HardDriveDownload className="size-4" />
          </span>
          <div>
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-fg">{t("enabled")}</p>
            <p id="plugin-cache-enabled-help" className="mt-1 text-xs text-fg-subtle">
              {t("enabledHelp")}
            </p>
          </div>
          <Switch
            id="plugin-cache-enabled"
            label={t("enabled")}
            descriptionId="plugin-cache-enabled-help"
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>

        <div>
          <Label htmlFor="plugin-cache-path">{t("path")}</Label>
          <Input
            id="plugin-cache-path"
            value={path}
            maxLength={1000}
            onChange={(event) => setPath(event.target.value)}
            placeholder="data/cache_serverplugins"
          />
          <p className="mt-1.5 text-xs text-fg-subtle">{t("pathHelp")}</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="plugin-cache-age">{t("maxAgeDays")}</Label>
            <Input
              id="plugin-cache-age"
              type="number"
              min={0}
              max={3650}
              value={maxAgeDays}
              onChange={(event) => setMaxAgeDays(event.target.value)}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">{t("zeroDisables")}</p>
          </div>
          <div>
            <Label htmlFor="plugin-cache-size">{t("maxMegabytes")}</Label>
            <Input
              id="plugin-cache-size"
              type="number"
              min={0}
              max={1048576}
              value={maxMegabytes}
              onChange={(event) => setMaxMegabytes(event.target.value)}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">{t("zeroDisables")}</p>
          </div>
        </div>

        <p className="rounded-md border border-line bg-surface-raised/40 px-4 py-3 text-sm text-fg-muted">
          {t("usage", {
            files: settings.pluginDownloadCacheFiles,
            size: megabytes(settings.pluginDownloadCacheBytes),
          })}
        </p>

        {message ? (
          <p role="status" className="text-sm text-fg-muted">
            {message}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={onSave} disabled={busy !== null}>
            <Save />
            {busy === "save" ? t("saving") : t("save")}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => onMaintenance("prune")}
            disabled={busy !== null}
          >
            <Brush />
            {t("prune")}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => onMaintenance("clear")}
            disabled={busy !== null || settings.pluginDownloadCacheFiles === 0}
          >
            <Trash2 />
            {t("clear")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
