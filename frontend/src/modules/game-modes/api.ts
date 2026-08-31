import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import {
  SERVER_OPERATION_ACTIONS,
  type ServerOperation,
  type ServerOperationAction,
} from "@/modules/servers/types";
import type {
  GameModeCatalog,
  GameModeId,
  GameModeMutation,
  GameModePlan,
  GameModeSummary,
  MutationStatus,
} from "@/modules/game-modes/types";

type MutationDto = {
  id: string;
  target: string;
  before?: unknown;
  after?: unknown;
  destructive: boolean;
  status: string;
};

type CatalogDto = {
  server_id: number;
  reachable: boolean;
  additional_parameters?: string | null;
  addons_path: string;
  addons_present?: boolean | null;
  swiftly_installed?: boolean | null;
  modes: Array<{
    id: string;
    launch_upsert: Record<string, string>;
    frameworks: string[];
    market_plugin_titles: string[];
    maps: Array<{ name: string; workshop_id: string }>;
    plugin_config: Record<string, boolean | number | string>;
    startup_workshop_map: string;
    present: Record<string, boolean | null>;
    missing_market_plugins?: string[];
  }>;
};

type PlanDto = {
  server_id: number;
  mode_id: string;
  wipe_addons: boolean;
  addons_path: string;
  startup: {
    before?: string | null;
    after?: string | null;
    changed: boolean;
  };
  mutations: MutationDto[];
  blocked: boolean;
  blocking_reasons: string[];
  warnings?: Array<{ rule_id: number; reason: string }>;
  plan_hash: string;
};

type OperationDto = {
  operation_id: string;
  server_id: number;
  action: string;
  status: ServerOperation["status"];
  success?: boolean | null;
  message?: string | null;
  server_status?: string | null;
  started_at: string;
  completed_at?: string | null;
  actor_user_id: number;
  stream_url: string;
  command?: string | null;
};

function toModeId(value: string): GameModeId {
  return value === "kz" ? "kz" : "kz";
}

function toMutationStatus(value: string): MutationStatus {
  if (value === "unchanged" || value === "already_present") return value;
  return "pending";
}

function toSummary(raw: CatalogDto["modes"][number]): GameModeSummary {
  return {
    id: toModeId(raw.id),
    launchUpsert: raw.launch_upsert,
    frameworks: raw.frameworks,
    marketPluginTitles: raw.market_plugin_titles,
    maps: raw.maps.map((item) => ({
      name: item.name,
      workshopId: item.workshop_id,
    })),
    pluginConfig: raw.plugin_config,
    startupWorkshopMap: raw.startup_workshop_map,
    present: {
      counterstrikesharp: raw.present.counterstrikesharp ?? null,
      cs2kzMetamod: raw.present["cs2kz-metamod"] ?? null,
      mapchooser: raw.present.mapchooser ?? null,
    },
    missingMarketPlugins: raw.missing_market_plugins ?? [],
  };
}

function toMutation(raw: MutationDto): GameModeMutation {
  return {
    id: raw.id,
    target: raw.target,
    before: raw.before ?? null,
    after: raw.after ?? null,
    destructive: raw.destructive,
    status: toMutationStatus(raw.status),
  };
}

function toPlan(raw: PlanDto): GameModePlan {
  return {
    serverId: raw.server_id,
    modeId: toModeId(raw.mode_id),
    wipeAddons: raw.wipe_addons,
    addonsPath: raw.addons_path,
    startup: {
      before: raw.startup.before ?? null,
      after: raw.startup.after ?? null,
      changed: raw.startup.changed,
    },
    mutations: raw.mutations.map(toMutation),
    blocked: raw.blocked,
    blockingReasons: raw.blocking_reasons,
    warnings: (raw.warnings ?? []).map((item) => ({
      ruleId: item.rule_id,
      reason: item.reason,
    })),
    planHash: raw.plan_hash,
  };
}

function toOperationAction(value: string): ServerOperationAction {
  return (SERVER_OPERATION_ACTIONS as readonly string[]).includes(value)
    ? (value as ServerOperationAction)
    : "install_game_mode";
}

function toOperation(raw: OperationDto): ServerOperation {
  return {
    operationId: raw.operation_id,
    serverId: raw.server_id,
    action: toOperationAction(raw.action),
    status: raw.status,
    success: raw.success ?? null,
    message: raw.message ?? null,
    serverStatus: null,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    actorUserId: raw.actor_user_id,
    streamUrl: raw.stream_url,
    command: raw.command ?? null,
  };
}

export async function getGameModeCatalog(
  serverId: number,
): Promise<ApiResult<GameModeCatalog>> {
  const result = await apiFetch<CatalogDto>(
    `/api/v1/servers/${serverId}/game-modes`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      serverId: result.data.server_id,
      reachable: result.data.reachable,
      additionalParameters: result.data.additional_parameters ?? null,
      addonsPath: result.data.addons_path,
      addonsPresent: result.data.addons_present ?? null,
      swiftlyInstalled: result.data.swiftly_installed ?? null,
      modes: result.data.modes.map(toSummary),
    },
  };
}

export async function preflightGameMode(
  serverId: number,
  modeId: GameModeId,
  wipeAddons: boolean,
): Promise<ApiResult<GameModePlan>> {
  const result = await apiFetch<PlanDto>(
    `/api/v1/servers/${serverId}/game-modes/${modeId}/preflight`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ wipe_addons: wipeAddons }),
      timeoutMs: 30_000,
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toPlan(result.data) };
}

export async function installGameMode(
  serverId: number,
  modeId: GameModeId,
  input: {
    readonly wipeAddons: boolean;
    readonly wipeAddonsAcknowledged: boolean;
    readonly planHash: string;
    readonly acknowledgeWarningRuleIds: readonly number[];
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<OperationDto>(
    `/api/v1/servers/${serverId}/game-modes/${modeId}/install`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        wipe_addons: input.wipeAddons,
        wipe_addons_acknowledged: input.wipeAddonsAcknowledged,
        plan_hash: input.planHash,
        acknowledge_warning_rule_ids: input.acknowledgeWarningRuleIds,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}
