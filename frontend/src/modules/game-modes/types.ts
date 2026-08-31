export type GameModeId = "kz";

export type GameModePresence = {
  readonly counterstrikesharp: boolean | null;
  readonly cs2kzMetamod: boolean | null;
  readonly mapchooser: boolean | null;
};

export type GameModeMap = {
  readonly name: string;
  readonly workshopId: string;
};

export type GameModeSummary = {
  readonly id: GameModeId;
  readonly launchUpsert: Readonly<Record<string, string>>;
  readonly frameworks: readonly string[];
  readonly marketPluginTitles: readonly string[];
  readonly maps: readonly GameModeMap[];
  readonly pluginConfig: Readonly<Record<string, boolean | number | string>>;
  readonly startupWorkshopMap: string;
  readonly present: GameModePresence;
  readonly missingMarketPlugins: readonly string[];
};

export type GameModeCatalog = {
  readonly serverId: number;
  readonly reachable: boolean;
  readonly additionalParameters: string | null;
  readonly addonsPath: string;
  readonly addonsPresent: boolean | null;
  readonly swiftlyInstalled: boolean | null;
  readonly modes: readonly GameModeSummary[];
};

export type MutationStatus = "pending" | "unchanged" | "already_present";

export type GameModeMutation = {
  readonly id: string;
  readonly target: string;
  readonly before: unknown;
  readonly after: unknown;
  readonly destructive: boolean;
  readonly status: MutationStatus;
};

export type GameModeConflict = {
  readonly ruleId: number;
  readonly reason: string;
};

export type GameModePlan = {
  readonly serverId: number;
  readonly modeId: GameModeId;
  readonly wipeAddons: boolean;
  readonly addonsPath: string;
  readonly startup: {
    readonly before: string | null;
    readonly after: string | null;
    readonly changed: boolean;
  };
  readonly mutations: readonly GameModeMutation[];
  readonly blocked: boolean;
  readonly blockingReasons: readonly string[];
  readonly warnings: readonly GameModeConflict[];
  readonly planHash: string;
};
