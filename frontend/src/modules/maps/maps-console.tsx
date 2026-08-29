"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import {
  Map as MapIcon,
  Plus,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import {
  addMapAction,
  applyMapPresetAction,
  deleteMapAction,
  runMapSyncAction,
  uninstallMapChooserAction,
  updateMapEnabledAction,
  updateMapPluginConfigAction,
  updateMapSyncAction,
} from "@/modules/maps/actions";
import { MAP_PRESETS, type MapEntry, type MapsWorkspace } from "@/modules/maps/types";
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
import { LinkButton } from "@/shared/ui/link-button";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function MapsConsole({ initial }: { initial: MapsWorkspace }) {
  const t = useTranslations("maps");
  const router = useRouter();
  const [workspace, setWorkspace] = useState(initial);
  const [pluginValues, setPluginValues] = useState<
    Record<string, boolean | number | string>
  >(() => valuesFromWorkspace(initial));
  const [syncUrl, setSyncUrl] = useState(initial.customSync.url);
  const [syncInterval, setSyncInterval] = useState(
    String(initial.customSync.intervalSeconds),
  );
  const [syncEnabled, setSyncEnabled] = useState(initial.customSync.enabled);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner | null>(
    initial.message ? { tone: "ok", text: initial.message } : null,
  );

  function applyWorkspace(next: MapsWorkspace) {
    setWorkspace(next);
    setPluginValues(valuesFromWorkspace(next));
    setSyncUrl(next.customSync.url);
    setSyncInterval(String(next.customSync.intervalSeconds));
    setSyncEnabled(next.customSync.enabled);
    if (next.message) setBanner({ tone: "ok", text: next.message });
  }

  async function run(
    key: string,
    work: () => Promise<
      | { ok: true; data: MapsWorkspace }
      | { ok: false; error: string; status: number }
    >,
  ) {
    setPending(key);
    setBanner(null);
    const result = await work();
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    applyWorkspace(result.data);
    router.refresh();
  }

  const canMutate = workspace.ready && Boolean(workspace.revision) && !pending;
  const serverId = workspace.serverId;
  const identity = (entry: MapEntry) => ({
    name: entry.name,
    workshopId: entry.workshopId,
    expectedRevision: workspace.revision ?? "",
  });

  return (
    <div className="space-y-6">
      {banner ? (
        <p
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" &&
              "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.text}
        </p>
      ) : null}

      <StatusCard workspace={workspace} />

      <Card data-testid="mapchooser-uninstall">
        <CardHeader>
          <div>
            <CardTitle>{t("uninstallTitle")}</CardTitle>
            <CardDescription>{t("uninstallWarning")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <Button
            type="button"
            variant="outline"
            disabled={!workspace.mapchooserInstalled || Boolean(pending)}
            onClick={() => {
              if (!window.confirm(t("uninstallConfirm"))) return;
              void run("uninstall-mapchooser", () =>
                uninstallMapChooserAction(serverId, "UNINSTALL MAPCHOOSER"),
              );
            }}
          >
            <Trash2 className="size-4" />
            {pending === "uninstall-mapchooser"
              ? t("uninstalling")
              : t("uninstallButton")}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("poolTitle")}</CardTitle>
            <CardDescription>{t("poolHelp")}</CardDescription>
          </div>
          <Badge tone="neutral">{t("mapCount", { count: workspace.maps.length })}</Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          {workspace.configError ? (
            <p className="flex items-start gap-2 text-sm text-warn">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              {workspace.configError}
            </p>
          ) : null}
          {!workspace.ready ? (
            <p className="text-sm text-fg-muted">{t("poolLocked")}</p>
          ) : workspace.maps.length === 0 ? (
            <p className="text-sm text-fg-muted">{t("poolEmpty")}</p>
          ) : (
            <ul className="divide-y divide-line">
              {workspace.maps.map((entry) => (
                <li
                  key={`${entry.name}:${entry.workshopId}`}
                  className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <div className="min-w-0 space-y-1">
                    <p className="truncate text-sm font-medium text-fg">{entry.name}</p>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-subtle">
                      {entry.workshopId ? (
                        <Badge tone="info">ID {entry.workshopId}</Badge>
                      ) : (
                        <Badge tone="neutral">{t("official")}</Badge>
                      )}
                      {entry.minPlayers ? (
                        <span>{t("minPlayers", { count: entry.minPlayers })}</span>
                      ) : null}
                      {entry.onlyNominate ? <span>{t("nominateOnly")}</span> : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Switch
                      id={`map-enabled-${entry.name}-${entry.workshopId || "official"}`}
                      checked={entry.enabled}
                      disabled={!canMutate}
                      label={t("enabled")}
                      onCheckedChange={(enabled) => {
                        void run(`toggle:${entry.name}`, () =>
                          updateMapEnabledAction(serverId, {
                            ...identity(entry),
                            enabled,
                          }),
                        );
                      }}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={!canMutate}
                      aria-label={t("remove")}
                      onClick={() => {
                        if (!window.confirm(t("removeConfirm", { name: entry.name }))) {
                          return;
                        }
                        void run(`delete:${entry.name}`, () =>
                          deleteMapAction(serverId, identity(entry)),
                        );
                      }}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <form
        className="grid gap-6 xl:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const workshopId = String(form.get("workshopId") ?? "").trim();
          if (!workshopId || !canMutate) return;
          const formEl = event.currentTarget;
          void run("add", async () => {
            const result = await addMapAction(serverId, {
              workshopId,
              name: String(form.get("mapName") ?? "").trim() || undefined,
              minPlayers: Number(form.get("minPlayers") || 0),
              onlyNominate: form.get("onlyNominate") === "on",
              restrictedTimes: String(form.get("restrictedTimes") ?? "").trim(),
            });
            if (result.ok) formEl.reset();
            return result;
          });
        }}
      >
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("addTitle")}</CardTitle>
              <CardDescription>{t("addHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label={t("fields.workshopId")}>
              <Input
                name="workshopId"
                required
                disabled={!canMutate}
                placeholder="3070591565"
              />
            </Field>
            <Field label={t("fields.mapName")}>
              <Input name="mapName" disabled={!canMutate} placeholder={t("fields.mapNameHint")} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.minPlayers")}>
                <Input
                  name="minPlayers"
                  type="number"
                  min={0}
                  max={64}
                  defaultValue={0}
                  disabled={!canMutate}
                />
              </Field>
              <Field label={t("fields.restrictedTimes")}>
                <Input
                  name="restrictedTimes"
                  disabled={!canMutate}
                  placeholder="01:00-08:00"
                />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm text-fg-muted">
              <input
                type="checkbox"
                name="onlyNominate"
                disabled={!canMutate}
                className="size-4 accent-primary"
              />
              {t("fields.onlyNominate")}
            </label>
            <Button type="submit" disabled={!canMutate}>
              <Plus />
              {pending === "add" ? t("adding") : t("add")}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("presetsTitle")}</CardTitle>
              <CardDescription>{t("presetsHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {MAP_PRESETS.map((preset) => (
              <Button
                key={preset}
                type="button"
                variant={preset === "official" ? "primary" : "outline"}
                disabled={!canMutate}
                onClick={() => {
                  if (!window.confirm(t(`presetConfirm.${preset}`))) return;
                  void run(`preset:${preset}`, () =>
                    applyMapPresetAction(serverId, {
                      preset,
                      expectedRevision: workspace.revision ?? "",
                      pluginConfigExpectedRevision:
                        workspace.pluginConfig?.revision || undefined,
                    }),
                  );
                }}
              >
                {t(`presets.${preset}`)}
              </Button>
            ))}
          </CardContent>
        </Card>
      </form>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("syncTitle")}</CardTitle>
            <CardDescription>{t("syncHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label={t("fields.syncUrl")}>
            <Input
              value={syncUrl}
              onChange={(event) => setSyncUrl(event.target.value)}
              disabled={Boolean(pending)}
              placeholder="https://"
            />
          </Field>
          <div className="flex flex-wrap items-end gap-4">
            <Field label={t("fields.interval")} className="w-40">
              <Input
                type="number"
                min={300}
                max={86400}
                value={syncInterval}
                onChange={(event) => setSyncInterval(event.target.value)}
                disabled={Boolean(pending)}
              />
            </Field>
            <div className="flex items-center gap-2 pb-1">
              <Switch
                id="map-sync-enabled"
                checked={syncEnabled}
                disabled={Boolean(pending)}
                label={t("syncEnabled")}
                onCheckedChange={setSyncEnabled}
              />
              <span className="text-sm text-fg-muted">{t("syncEnabled")}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={Boolean(pending) || !syncUrl.trim()}
              onClick={() => {
                void run("sync-save", () =>
                  updateMapSyncAction(serverId, {
                    url: syncUrl.trim(),
                    intervalSeconds: Number(syncInterval) || 300,
                    enabled: syncEnabled,
                  }),
                );
              }}
            >
              {pending === "sync-save" ? t("saving") : t("saveSync")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!canMutate || !workspace.customSync.url}
              onClick={() => {
                if (!window.confirm(t("runSyncConfirm"))) return;
                void run("sync-run", () =>
                  runMapSyncAction(serverId, workspace.revision ?? ""),
                );
              }}
            >
              <RefreshCw />
              {pending === "sync-run" ? t("syncing") : t("runSync")}
            </Button>
          </div>
          {workspace.customSync.lastStatus ? (
            <p className="text-xs text-fg-subtle">
              {t("syncStatus", {
                status: workspace.customSync.lastStatus,
                count: workspace.customSync.runCount,
              })}
              {workspace.customSync.lastError
                ? ` — ${workspace.customSync.lastError}`
                : ""}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {workspace.pluginConfig ? (
        <form
          onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            if (!canMutate || !workspace.pluginConfig) return;
            void run("plugin-config", () =>
              updateMapPluginConfigAction(serverId, {
                values: pluginValues,
                expectedRevision: workspace.pluginConfig?.revision,
              }),
            );
          }}
        >
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("pluginTitle")}</CardTitle>
                <CardDescription>{t("pluginHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {workspace.pluginConfig.configError ? (
                <p className="text-sm text-warn">{workspace.pluginConfig.configError}</p>
              ) : null}
              <div className="grid gap-4 md:grid-cols-2">
                {workspace.pluginConfig.fields.map((field) => (
                  <div key={field.key} className="space-y-1.5">
                    <Label htmlFor={`plugin-${field.key}`}>
                      {field.key}
                      <span className="ml-2 text-xs font-normal text-fg-subtle">
                        {pluginGroupLabel(t, field.group)}
                      </span>
                    </Label>
                    {field.kind === "boolean" ? (
                      <Switch
                        id={`plugin-${field.key}`}
                        checked={Boolean(pluginValues[field.key])}
                        disabled={!canMutate}
                        label={field.key}
                        onCheckedChange={(next) =>
                          setPluginValues((current) => ({
                            ...current,
                            [field.key]: next,
                          }))
                        }
                      />
                    ) : (
                      <Input
                        id={`plugin-${field.key}`}
                        type={field.kind === "string" ? "text" : "number"}
                        step={field.kind === "integer" ? 1 : "any"}
                        disabled={!canMutate}
                        value={String(pluginValues[field.key] ?? "")}
                        onChange={(event) => {
                          const raw = event.target.value;
                          setPluginValues((current) => ({
                            ...current,
                            [field.key]:
                              field.kind === "integer"
                                ? Number.parseInt(raw, 10) || 0
                                : field.kind === "number"
                                  ? Number(raw) || 0
                                  : raw,
                          }));
                        }}
                      />
                    )}
                  </div>
                ))}
              </div>
              <Button type="submit" disabled={!canMutate}>
                {pending === "plugin-config" ? t("saving") : t("savePlugin")}
              </Button>
            </CardContent>
          </Card>
        </form>
      ) : null}
    </div>
  );
}

const PLUGIN_GROUPS = [
  "vote",
  "extend",
  "mapPool",
  "rtv",
  "mapChange",
  "display",
  "other",
] as const;

function pluginGroupLabel(
  t: (key: `groups.${(typeof PLUGIN_GROUPS)[number]}`) => string,
  group: string,
): string {
  if ((PLUGIN_GROUPS as readonly string[]).includes(group)) {
    return t(`groups.${group as (typeof PLUGIN_GROUPS)[number]}`);
  }
  return group;
}

function valuesFromWorkspace(workspace: MapsWorkspace) {
  return Object.fromEntries(
    (workspace.pluginConfig?.fields ?? []).map((field) => [field.key, field.value]),
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function StatusCard({ workspace }: { workspace: MapsWorkspace }) {
  const t = useTranslations("maps");
  const marketHref =
    `/plugins?q=${encodeURIComponent(workspace.pluginCenterName || "MapChooser")}&serverId=${workspace.serverId}` as Route;
  const operationsHref = `/servers/${workspace.serverId}/operations` as Route;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <MapIcon className="size-4 text-primary" />
            {t("statusTitle")}
          </CardTitle>
          <CardDescription>{t("statusHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Badge tone={workspace.sshOk ? "ok" : "danger"}>
            {workspace.sshOk ? t("sshOk") : t("sshDown")}
          </Badge>
          <Badge tone={workspace.counterStrikeSharpInstalled ? "ok" : "warn"}>
            {workspace.counterStrikeSharpInstalled ? t("cssReady") : t("cssMissing")}
          </Badge>
          <Badge tone={workspace.mapchooserInstalled ? "ok" : "warn"}>
            {workspace.mapchooserInstalled ? t("chooserReady") : t("chooserMissing")}
          </Badge>
        </div>
        {!workspace.sshOk ? (
          <p className="text-sm text-danger">{workspace.sshError || t("sshDownHelp")}</p>
        ) : null}
        {workspace.sshOk && !workspace.counterStrikeSharpInstalled ? (
          <div className="flex flex-wrap items-center gap-3 text-sm text-fg-muted">
            <span>{t("installCssHelp")}</span>
            <LinkButton href={operationsHref} size="sm" variant="outline">
              {t("goOperations")}
            </LinkButton>
          </div>
        ) : null}
        {workspace.sshOk &&
        workspace.counterStrikeSharpInstalled &&
        !workspace.mapchooserInstalled ? (
          <div className="flex flex-wrap items-center gap-3 text-sm text-fg-muted">
            <span>{t("installChooserHelp")}</span>
            <LinkButton href={marketHref} size="sm" variant="outline">
              {t("goMarket")}
            </LinkButton>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
