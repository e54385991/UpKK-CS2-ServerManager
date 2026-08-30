export const MAP_PRESETS = ["official", "kz", "ze"] as const;

export type MapPreset = (typeof MAP_PRESETS)[number];

export type MapEntry = {
  readonly name: string;
  readonly workshopId: string;
  readonly enabled: boolean;
  readonly filename: string;
  readonly minPlayers: string;
  readonly onlyNominate: boolean;
  readonly restrictedTimes: string;
};

export type MapPluginField = {
  readonly key: string;
  readonly kind: string;
  readonly value: boolean | number | string;
  readonly group: string;
  readonly known: boolean;
};

export type MapPluginConfig = {
  readonly revision: string;
  readonly fileExists: boolean;
  readonly fields: readonly MapPluginField[];
  readonly unsupportedFields: readonly string[];
  readonly configError: string | null;
};

export type MapSync = {
  readonly url: string;
  readonly enabled: boolean;
  readonly intervalSeconds: number;
  readonly lastRun: string | null;
  readonly nextRun: string | null;
  readonly lastStatus: string | null;
  readonly lastError: string | null;
  readonly runCount: number;
};

export type MapsWorkspace = {
  readonly serverId: number;
  readonly sshOk: boolean;
  readonly sshError: string | null;
  readonly ready: boolean;
  readonly counterStrikeSharpInstalled: boolean;
  readonly mapchooserInstalled: boolean;
  readonly mapsFileExists: boolean;
  readonly pluginConfigFileExists: boolean;
  readonly mapsPath: string | null;
  readonly pluginConfigPath: string | null;
  readonly pluginCenterName: string | null;
  readonly maps: readonly MapEntry[];
  readonly revision: string | null;
  readonly configError: string | null;
  readonly pluginConfig: MapPluginConfig | null;
  readonly customSync: MapSync;
  readonly message: string | null;
};

export type MapAddInput = {
  readonly workshopId: string;
  readonly name?: string;
  readonly enabled?: boolean;
  readonly minPlayers?: number;
  readonly onlyNominate?: boolean;
  readonly restrictedTimes?: string;
};

export type MapIdentityInput = {
  readonly name: string;
  readonly workshopId: string;
  readonly expectedRevision: string;
};
