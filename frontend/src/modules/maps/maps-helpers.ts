import type { Route } from "next";
import type { MapPluginField, MapsWorkspace } from "@/modules/maps/types";

export const MAPCHOOSER_PLUGIN_NAME = "CS2-Upkk-PanelPLG-Mapchooser";

export function mapchooserMarketHref(
  serverId: number,
  pluginName: string | null,
): Route {
  return `/plugins?q=${encodeURIComponent(pluginName || MAPCHOOSER_PLUGIN_NAME)}&serverId=${serverId}` as Route;
}

export const PLUGIN_GROUPS = [
  "vote",
  "extend",
  "mapPool",
  "rtv",
  "mapChange",
  "display",
  "other",
] as const;

export const PLUGIN_FIELD_KEYS = [
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

export type PluginFieldKey = (typeof PLUGIN_FIELD_KEYS)[number];

export function pluginFieldKey(key: string): PluginFieldKey | null {
  return (PLUGIN_FIELD_KEYS as readonly string[]).includes(key)
    ? (key as PluginFieldKey)
    : null;
}

export function pluginGroupLabel(
  t: (key: `groups.${(typeof PLUGIN_GROUPS)[number]}`) => string,
  group: string,
): string {
  if ((PLUGIN_GROUPS as readonly string[]).includes(group)) {
    return t(`groups.${group as (typeof PLUGIN_GROUPS)[number]}`);
  }
  return group;
}

export function groupPluginFields(
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

export function valuesFromWorkspace(workspace: MapsWorkspace) {
  return Object.fromEntries(
    (workspace.pluginConfig?.fields ?? []).map((field) => [field.key, field.value]),
  );
}
