"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import { Trash2 } from "lucide-react";
import {
  mapchooserMarketHref,
  pluginFieldKey,
} from "@/modules/maps/maps-helpers";
import type { MapEntry, MapPluginField, MapsWorkspace } from "@/modules/maps/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";
import { LinkButton } from "@/shared/ui/link-button";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

export function MapTile({
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
          {minPlayersLabel ? <span className="ml-1">{minPlayersLabel}</span> : null}
          {entry.onlyNominate ? <span className="ml-1">{nominateOnlyLabel}</span> : null}
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

export function Field({
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

export function PluginFieldInput({
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
                <code className="ml-1.5 text-[11px] font-normal text-fg-subtle">{field.key}</code>
              ) : null}
            </Label>
            {description ? <p className="mt-1 text-xs text-fg-subtle">{description}</p> : null}
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
          <code className="ml-1.5 text-[11px] font-normal text-fg-subtle">{field.key}</code>
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
      {description ? <p className="mt-1 text-xs text-fg-subtle">{description}</p> : null}
    </div>
  );
}

export function StatusBar({ workspace }: { workspace: MapsWorkspace }) {
  const t = useTranslations("maps");
  const marketHref = mapchooserMarketHref(
    workspace.serverId,
    workspace.pluginCenterName,
  );
  const operationsHref = `/servers/${workspace.serverId}/operations` as Route;
  const blocked =
    !workspace.sshOk || !workspace.counterStrikeSharpInstalled || !workspace.mapchooserInstalled;

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
          {workspace.sshOk && workspace.counterStrikeSharpInstalled && !workspace.mapchooserInstalled ? (
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
