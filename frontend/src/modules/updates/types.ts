export type InstalledVersionSource = "steam.inf" | "database" | "unknown";

export type GameUpdates = {
  readonly installedVersion: string | null;
  readonly installedBuildId: string | null;
  readonly installedSource: InstalledVersionSource;
  readonly advertisedVersion: string | null;
  readonly upToDate: boolean | null;
  readonly steamCheckOk: boolean;
  readonly steamMessage: string | null;
  readonly steamError: string | null;
  readonly enableAutoUpdate: boolean;
  readonly intervalHours: number;
  readonly lastUpdateCheck: string | null;
  readonly lastUpdateTime: string | null;
  readonly currentGameVersion: string | null;
};

export type GameUpdateAction = "update" | "validate";

export type ManagedPluginSourceType = "github" | "market" | "framework";

export type ManagedUpdatePlugin = {
  readonly id: number;
  readonly displayName: string;
  readonly sourceType: string;
  readonly installedVersion: string;
  readonly latestVersion: string | null;
  readonly autoUpdateEnabled: boolean;
  readonly lastStatus: string | null;
  readonly lastError: string | null;
  readonly lastCheckAt: string | null;
  readonly excludeDirs: readonly string[];
  readonly excludeFiles: readonly string[];
  readonly backupBeforeUpdate: boolean;
  readonly restartAfterUpdate: boolean;
};

export type PluginUpdates = {
  readonly enableAutoUpdate: boolean;
  readonly intervalHours: number;
  readonly lastCheck: string | null;
  readonly enablePostCommands: boolean;
  readonly commandIds: readonly number[];
  readonly plugins: readonly ManagedUpdatePlugin[];
};

export type PluginUpdateStatus = {
  readonly state: string;
  readonly phase: string;
  readonly message: string | null;
  readonly current: number;
  readonly total: number;
  readonly logs: readonly { time: string | null; message: string }[];
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
};

export type RegisterMarketOption = {
  readonly id: number;
  readonly title: string;
  readonly version: string | null;
  readonly githubUrl: string;
};

export type RegisterReleaseAsset = {
  readonly name: string;
};

export type RegisterRelease = {
  readonly id: string | null;
  readonly tagName: string;
  readonly prerelease: boolean;
  readonly assets: readonly RegisterReleaseAsset[];
};

export type ManagedPluginRegisterInput = {
  readonly sourceType: ManagedPluginSourceType;
  readonly displayName: string;
  readonly repoUrl?: string | null;
  readonly marketPluginId?: number | null;
  readonly frameworkKey?: string | null;
  readonly installedReleaseId?: string | null;
  readonly installedVersion?: string;
  readonly assetGlob?: string | null;
  readonly customInstallPath?: string | null;
  readonly excludeDirs?: readonly string[];
  readonly excludeFiles?: readonly string[];
};
