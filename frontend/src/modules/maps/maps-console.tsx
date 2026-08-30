"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import {
  Map as MapIcon,
  Plus,
  RefreshCw,
  Search,
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
import {
  MAP_PRESETS,
  type MapEntry,
  type MapPluginField,
  type MapsWorkspace,
} from "@/modules/maps/types";
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
import { Input, Label } from "@/shared/ui/input";
import { LinkButton } from "@/shared/ui/link-button";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };
type ExtraSection = "sync" | "plugin" | "more";
type PoolFilter = "all" | "on" | "off";

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
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<PoolFilter>("all");
  const [extra, setExtra] = useState<ExtraSection>("sync");
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

  const visibleMaps = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return workspace.maps.filter((entry) => {
      if (filter === "on" && !entry.enabled) return false;
      if (filter === "off" && entry.enabled) return false;
      if (!needle) return true;
      return (
        entry.name.toLowerCase().includes(needle) ||
        entry.workshopId.toLowerCase().includes(needle)
      );
    });
  }, [filter, query, workspace.maps]);

  const extras: { id: ExtraSection; label: string }[] = [
    { id: "sync", label: t("extrasSync") },
    { id: "plugin", label: t("extrasPlugin") },
    { id: "more", label: t("extrasMore") },
  ];

  return (
    <div className="space-y-4">
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

      <StatusBar workspace={workspace} />
      <p className="text-sm text-fg-muted">{t("pageHelp")}</p>

      <form
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
          <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <MapIcon className="size-4 text-primary" />
                {t("poolTitle")}
              </CardTitle>
              <CardDescription>{t("poolHelp")}</CardDescription>
            </div>
            <Badge tone="neutral">{t("mapCount", { count: workspace.maps.length })}</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 lg:flex-row">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t("searchMaps")}
                  className="pl-9"
                  aria-label={t("searchMaps")}
                />
              </div>
              <div className="flex flex-wrap gap-1">
                {(["all", "on", "off"] as const).map((id) => (
                  <Button
                    key={id}
                    type="button"
                    size="sm"
                    variant={filter === id ? "secondary" : "ghost"}
                    onClick={() => setFilter(id)}
                  >
                    {t(`filter.${id}`)}
                  </Button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                name="workshopId"
                required
                disabled={!canMutate}
                placeholder={t("fields.workshopId")}
                className="sm:flex-1"
                aria-label={t("fields.workshopId")}
              />
              <Button type="submit" disabled={!canMutate} className="shrink-0">
                <Plus />
                {pending === "add" ? t("adding") : t("add")}
              </Button>
            </div>

            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-fg-subtle">{t("presetsTitle")}</span>
                {MAP_PRESETS.map((preset) => (
                  <Button
                    key={preset}
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!canMutate}
                    title={t(`presetNotes.${preset}`)}
                    onClick={() => {
                      void (async () => {
                        if (!(await confirm(t(`presetConfirm.${preset}`)))) return;
                        void run(`preset:${preset}`, () =>
                          applyMapPresetAction(serverId, {
                            preset,
                            expectedRevision: workspace.revision ?? "",
                            pluginConfigExpectedRevision:
                              workspace.pluginConfig?.revision || undefined,
                          }),
                        );
                      })();
                    }}
                  >
                    {t(`presets.${preset}`)}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-fg-subtle">{t("presetsHelp")}</p>
            </div>

            <details className="rounded-md border border-line px-3 py-2">
              <summary className="cursor-pointer text-sm text-fg-muted">
                {t("addAdvanced")}
              </summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Field label={t("fields.mapName")}>
                  <Input name="mapName" disabled={!canMutate} placeholder={t("fields.mapNameHint")} />
                </Field>
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
                <label className="flex items-center gap-2 self-end pb-1 text-sm text-fg-muted">
                  <input
                    type="checkbox"
                    name="onlyNominate"
                    disabled={!canMutate}
                    className="size-4 accent-primary"
                  />
                  {t("fields.onlyNominate")}
                </label>
              </div>
            </details>

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
            ) : visibleMaps.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("noMatch")}</p>
            ) : (
              <ul
                className="grid max-h-[36rem] grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-2 overflow-y-auto"
                data-testid="map-pool"
              >
                {visibleMaps.map((entry) => (
                  <li key={`${entry.name}:${entry.workshopId}`}>
                    <MapTile
                      entry={entry}
                      canMutate={canMutate}
                      enabledLabel={t("enabled")}
                      officialLabel={t("official")}
                      nominateOnlyLabel={t("nominateOnly")}
                      minPlayersLabel={
                        entry.minPlayers
                          ? t("minPlayers", { count: entry.minPlayers })
                          : null
                      }
                      removeLabel={t("remove")}
                      onToggle={(enabled) => {
                        void run(`toggle:${entry.name}`, () =>
                          updateMapEnabledAction(serverId, {
                            ...identity(entry),
                            enabled,
                          }),
                        );
                      }}
                      onRemove={() => {
                        void (async () => {
                          if (
                            !(await confirm(
                              t("removeConfirm", { name: entry.name }),
                            ))
                          ) {
                            return;
                          }
                          void run(`delete:${entry.name}`, () =>
                            deleteMapAction(serverId, identity(entry)),
                          );
                        })();
                      }}
                    />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </form>

      <Card data-testid="mapchooser-uninstall">
        <CardHeader className="pb-3">
          <div
            role="tablist"
            aria-label={t("extrasLabel")}
            className="flex rounded-md border border-line bg-surface-raised p-0.5"
          >
            {extras.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={extra === item.id}
                className={cn(
                  "flex-1 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-colors",
                  extra === item.id
                    ? "bg-surface text-fg shadow-sm"
                    : "text-fg-muted hover:text-fg",
                )}
                onClick={() => setExtra(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {extra === "sync" ? (
            <div className="space-y-3">
              <p className="text-sm text-fg-muted">{t("syncHelp")}</p>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem_auto]">
                <Field label={t("fields.syncUrl")}>
                  <Input
                    value={syncUrl}
                    onChange={(event) => setSyncUrl(event.target.value)}
                    disabled={Boolean(pending)}
                    placeholder="https://"
                  />
                </Field>
                <Field label={t("fields.interval")}>
                  <Input
                    type="number"
                    min={300}
                    max={86400}
                    value={syncInterval}
                    onChange={(event) => setSyncInterval(event.target.value)}
                    disabled={Boolean(pending)}
                  />
                </Field>
                <div className="flex items-end gap-2 pb-1">
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
                  size="sm"
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
                  size="sm"
                  variant="outline"
                  disabled={!canMutate || !workspace.customSync.url}
                  onClick={() => {
                    void (async () => {
                      if (!(await confirm(t("runSyncConfirm")))) return;
                      void run("sync-run", () =>
                        runMapSyncAction(serverId, workspace.revision ?? ""),
                      );
                    })();
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
            </div>
          ) : null}

          {extra === "plugin" ? (
            workspace.pluginConfig ? (
              <form
                className="space-y-3"
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
                <p className="text-sm text-fg-muted">{t("pluginHelp")}</p>
                {workspace.pluginConfigPath ? (
                  <p className="text-xs text-fg-subtle">
                    {t("pluginConfigPath")}{" "}
                    <code className="break-all">{workspace.pluginConfigPath}</code>
                  </p>
                ) : null}
                {!workspace.pluginConfig.fileExists ? (
                  <p className="text-sm text-fg-muted">{t("pluginConfigWillCreate")}</p>
                ) : null}
                {workspace.pluginConfig.unsupportedFields.length > 0 ? (
                  <p className="text-sm text-warn">
                    {t("unsupportedFields")}{" "}
                    <code className="break-all">
                      {workspace.pluginConfig.unsupportedFields.join(", ")}
                    </code>
                  </p>
                ) : null}
                {workspace.pluginConfig.configError ? (
                  <p className="text-sm text-warn">{workspace.pluginConfig.configError}</p>
                ) : null}
                {groupPluginFields(workspace.pluginConfig.fields).map(([group, fields], index) => (
                  <details
                    key={group}
                    className="rounded-md border border-line px-3 py-2"
                    open={index === 0}
                  >
                    <summary className="cursor-pointer text-sm font-medium text-fg">
                      {pluginGroupLabel(t, group)}
                      <span className="ml-2 text-xs font-normal text-fg-subtle">
                        {t("groupCount", { count: fields.length })}
                      </span>
                    </summary>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      {fields.map((field) => (
                        <div
                          key={field.key}
                          className={field.kind === "boolean" ? "md:col-span-2" : undefined}
                        >
                          <PluginFieldInput
                            field={field}
                            value={pluginValues[field.key]}
                            disabled={!canMutate}
                            onChange={(next) =>
                              setPluginValues((current) => ({
                                ...current,
                                [field.key]: next,
                              }))
                            }
                          />
                        </div>
                      ))}
                    </div>
                  </details>
                ))}
                <p className="text-xs text-fg-subtle">{t("pluginReloadHint")}</p>
                <Button type="submit" size="sm" disabled={!canMutate}>
                  {pending === "plugin-config" ? t("saving") : t("savePlugin")}
                </Button>
              </form>
            ) : (
              <p className="text-sm text-fg-muted">{t("pluginUnavailable")}</p>
            )
          ) : null}

          {extra === "more" ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium text-fg">{t("uninstallTitle")}</p>
                <p className="text-sm text-fg-muted">{t("uninstallWarning")}</p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!workspace.mapchooserInstalled || Boolean(pending)}
                onClick={() => {
                  void (async () => {
                    if (!(await confirm(t("uninstallConfirm")))) return;
                    void run("uninstall-mapchooser", () =>
                      uninstallMapChooserAction(serverId, "UNINSTALL MAPCHOOSER"),
                    );
                  })();
                }}
              >
                <Trash2 className="size-4" />
                {pending === "uninstall-mapchooser"
                  ? t("uninstalling")
                  : t("uninstallButton")}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
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

const PLUGIN_FIELD_KEYS = [
  "VoteStartTime",
  "AllowExtend",
  "ExtendTimeStep",
  "ExtendLimit",
  "ExcludeMaps",
  "IncludeMaps",
  "IncludeCurrent",
  "DontChangeRTV",
  "VoteDuration",
  "IgnoreSpec",
  "AllowRtv",
  "UseGameTimeLimit",
  "RTVPercent",
  "RTVDelay",
  "EnforceTimeLimit",
  "ChangeMapUse_host_workshop_map",
  "DisplayHudTimeleftRemaining",
  "RunOfFVote",
  "VotePercent",
  "AutoDownload",
  "VoteStartSound",
] as const;

type PluginFieldKey = (typeof PLUGIN_FIELD_KEYS)[number];

function pluginFieldKey(key: string): PluginFieldKey | null {
  return (PLUGIN_FIELD_KEYS as readonly string[]).includes(key)
    ? (key as PluginFieldKey)
    : null;
}

function pluginGroupLabel(
  t: (key: `groups.${(typeof PLUGIN_GROUPS)[number]}`) => string,
  group: string,
): string {
  if ((PLUGIN_GROUPS as readonly string[]).includes(group)) {
    return t(`groups.${group as (typeof PLUGIN_GROUPS)[number]}`);
  }
  return group;
}

function groupPluginFields(
  fields: readonly MapPluginField[],
): readonly (readonly [string, readonly MapPluginField[]])[] {
  const buckets = new Map<string, MapPluginField[]>();
  for (const field of fields) {
    const group = (PLUGIN_GROUPS as readonly string[]).includes(field.group)
      ? field.group
      : "other";
    const list = buckets.get(group) ?? [];
    list.push(field);
    buckets.set(group, list);
  }
  return PLUGIN_GROUPS.filter((group) => buckets.has(group)).map((group) => [
    group,
    buckets.get(group) ?? [],
  ]);
}

function valuesFromWorkspace(workspace: MapsWorkspace) {
  return Object.fromEntries(
    (workspace.pluginConfig?.fields ?? []).map((field) => [field.key, field.value]),
  );
}

function MapTile({
  entry,
  canMutate,
  enabledLabel,
  officialLabel,
  nominateOnlyLabel,
  minPlayersLabel,
  removeLabel,
  onToggle,
  onRemove,
}: {
  entry: MapEntry;
  canMutate: boolean;
  enabledLabel: string;
  officialLabel: string;
  nominateOnlyLabel: string;
  minPlayersLabel: string | null;
  removeLabel: string;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
}) {
  return (
    <div
      className={cn(
        "flex h-full flex-col gap-1.5 rounded-md border border-line bg-surface-raised px-2.5 py-2",
        !entry.enabled && "opacity-70",
      )}
    >
      <p className="truncate text-sm font-medium text-fg" title={entry.name}>
        {entry.name}
      </p>
      <div className="mt-auto flex items-center gap-1">
        <div className="min-w-0 flex-1 truncate text-[11px] text-fg-subtle">
          {entry.workshopId ? (
            <Badge tone="info" className="px-1.5 py-0">
              ID {entry.workshopId}
            </Badge>
          ) : (
            <Badge tone="neutral" className="px-1.5 py-0">
              {officialLabel}
            </Badge>
          )}
          {minPlayersLabel ? (
            <span className="ml-1">{minPlayersLabel}</span>
          ) : null}
          {entry.onlyNominate ? (
            <span className="ml-1">{nominateOnlyLabel}</span>
          ) : null}
        </div>
        <Switch
          id={`map-enabled-${entry.name}-${entry.workshopId || "official"}`}
          checked={entry.enabled}
          disabled={!canMutate}
          label={enabledLabel}
          onCheckedChange={onToggle}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-8"
          disabled={!canMutate}
          aria-label={removeLabel}
          onClick={onRemove}
        >
          <Trash2 />
        </Button>
      </div>
    </div>
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

function PluginFieldInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: MapPluginField;
  value: boolean | number | string | undefined;
  disabled: boolean;
  onChange: (next: boolean | number | string) => void;
}) {
  const t = useTranslations("maps");
  const copyKey = pluginFieldKey(field.key);
  const label = copyKey ? t(`pluginFields.${copyKey}.label`) : field.key;
  const description = copyKey ? t(`pluginFields.${copyKey}.description`) : "";
  const inputId = `plugin-${field.key}`;

  if (field.kind === "boolean") {
    return (
      <div className="rounded-md border border-line px-3 py-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Label htmlFor={inputId} className="mb-0">
              {label}
              {copyKey ? (
                <code className="ml-1.5 text-[11px] font-normal text-fg-subtle">
                  {field.key}
                </code>
              ) : null}
            </Label>
            {description ? (
              <p className="mt-1 text-xs text-fg-subtle">{description}</p>
            ) : null}
          </div>
          <Switch
            id={inputId}
            checked={Boolean(value)}
            disabled={disabled}
            label={label}
            onCheckedChange={onChange}
          />
        </div>
      </div>
    );
  }
  return (
    <div>
      <Label htmlFor={inputId}>
        {label}
        {copyKey ? (
          <code className="ml-1.5 text-[11px] font-normal text-fg-subtle">
            {field.key}
          </code>
        ) : null}
      </Label>
      <Input
        id={inputId}
        type={field.kind === "string" ? "text" : "number"}
        step={field.kind === "integer" ? 1 : "any"}
        disabled={disabled}
        value={String(value ?? "")}
        onChange={(event) => {
          const raw = event.target.value;
          onChange(
            field.kind === "integer"
              ? Number.parseInt(raw, 10) || 0
              : field.kind === "number"
                ? Number(raw) || 0
                : raw,
          );
        }}
      />
      {description ? (
        <p className="mt-1 text-xs text-fg-subtle">{description}</p>
      ) : null}
    </div>
  );
}

function StatusBar({ workspace }: { workspace: MapsWorkspace }) {
  const t = useTranslations("maps");
  const marketHref =
    `/plugins?q=${encodeURIComponent(workspace.pluginCenterName || "MapChooser")}&serverId=${workspace.serverId}` as Route;
  const operationsHref = `/servers/${workspace.serverId}/operations` as Route;
  const blocked =
    !workspace.sshOk ||
    !workspace.counterStrikeSharpInstalled ||
    !workspace.mapchooserInstalled;

  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3 shadow-panel">
      <div className="flex flex-wrap items-center gap-2">
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
      {blocked ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-fg-muted">
          {!workspace.sshOk ? (
            <span className="text-danger">{workspace.sshError || t("sshDownHelp")}</span>
          ) : null}
          {workspace.sshOk && !workspace.counterStrikeSharpInstalled ? (
            <>
              <span>{t("installCssHelp")}</span>
              <LinkButton href={operationsHref} size="sm" variant="outline">
                {t("goOperations")}
              </LinkButton>
            </>
          ) : null}
          {workspace.sshOk &&
          workspace.counterStrikeSharpInstalled &&
          !workspace.mapchooserInstalled ? (
            <>
              <span>{t("installChooserHelp")}</span>
              <LinkButton href={marketHref} size="sm" variant="outline">
                {t("goMarket")}
              </LinkButton>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
