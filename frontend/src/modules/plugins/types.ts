export const PLUGIN_CATEGORIES = [
  "game_mode",
  "entertainment",
  "utility",
  "admin",
  "performance",
  "library",
  "other",
] as const;

export type PluginCategory = (typeof PLUGIN_CATEGORIES)[number];

export function isPluginCategory(value: string): value is PluginCategory {
  return (PLUGIN_CATEGORIES as readonly string[]).includes(value);
}

/**
 * A listing's runtime. `counterstrikesharp` and `swiftly` mirror the mutually
 * exclusive CS2 plugin runtimes in `@/modules/servers/frameworks` (`swiftly` is
 * the key the rest of the panel already uses for SwiftlyS2). `other` marks a
 * listing that belongs to neither: it appears in both marketplace sections and
 * is exempt from the install-time runtime check.
 */
export const PLUGIN_FRAMEWORKS = [
  "counterstrikesharp",
  "swiftly",
  "other",
] as const;

export type PluginFramework = (typeof PLUGIN_FRAMEWORKS)[number];

/** The two sections the marketplace is browsed by. */
export const PLUGIN_FRAMEWORK_SECTIONS = ["counterstrikesharp", "swiftly"] as const;

export type PluginFrameworkSection = (typeof PLUGIN_FRAMEWORK_SECTIONS)[number];

export const DEFAULT_PLUGIN_FRAMEWORK: PluginFrameworkSection = "counterstrikesharp";

export function isPluginFramework(value: string): value is PluginFramework {
  return (PLUGIN_FRAMEWORKS as readonly string[]).includes(value);
}

export function isPluginFrameworkSection(
  value: string,
): value is PluginFrameworkSection {
  return (PLUGIN_FRAMEWORK_SECTIONS as readonly string[]).includes(value);
}

export function toPluginFramework(value: string | null | undefined): PluginFramework {
  return value && isPluginFramework(value) ? value : DEFAULT_PLUGIN_FRAMEWORK;
}

/** Resolve the browsed section, falling back to the default section. */
export function toPluginFrameworkSection(
  value: string | null | undefined,
): PluginFrameworkSection {
  return value && isPluginFrameworkSection(value) ? value : DEFAULT_PLUGIN_FRAMEWORK;
}

export type PluginRef = {
  readonly id: number;
  readonly title: string;
};

export type MarketInstallServer = {
  readonly id: number;
  readonly name: string;
  readonly usePanelProxy?: boolean;
  readonly githubProxy?: string | null;
};

export type MarketPlugin = {
  readonly id: number;
  readonly title: string;
  readonly description: string | null;
  readonly author: string | null;
  readonly version: string | null;
  readonly category: string;
  readonly framework: PluginFramework;
  readonly tags: string | null;
  readonly isRecommended: boolean;
  readonly iconUrl: string | null;
  readonly githubUrl: string;
  readonly customInstallPath: string | null;
  readonly downloadCount: number;
  readonly installCount: number;
  readonly dependencies: readonly PluginRef[];
};

export type PluginCategoryOption = {
  readonly value: string;
  readonly name: string;
};

export type MarketPluginPage = {
  readonly items: MarketPlugin[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
};

export type MarketPluginCreateInput = {
  readonly githubUrl: string;
  readonly title?: string | null;
  readonly description?: string | null;
  readonly author?: string | null;
  readonly version?: string | null;
  readonly category: PluginCategory;
  readonly framework: PluginFramework;
  readonly tags?: string | null;
  readonly isRecommended?: boolean;
  readonly iconUrl?: string | null;
  readonly dependencyIds?: readonly number[];
  readonly customInstallPath?: string | null;
};

/**
 * Partial marketplace edit. Only the keys present are sent, so an omitted
 * field keeps its stored value; an empty string clears an optional text field.
 */
export type MarketPluginUpdateInput = {
  readonly title?: string;
  readonly description?: string;
  readonly author?: string;
  readonly version?: string;
  readonly category?: PluginCategory;
  readonly framework?: PluginFramework;
  readonly tags?: string;
  readonly isRecommended?: boolean;
  readonly iconUrl?: string;
  readonly dependencyIds?: readonly number[];
  readonly customInstallPath?: string;
};

export const DESCRIPTION_SYNC_ACTIONS = [
  "updated",
  "unchanged",
  "skipped",
  "failed",
] as const;

export type DescriptionSyncAction = (typeof DESCRIPTION_SYNC_ACTIONS)[number];

export type DescriptionSyncItem = {
  readonly pluginId: number;
  readonly title: string;
  readonly githubUrl: string;
  readonly action: DescriptionSyncAction;
  readonly message: string | null;
};

export type DescriptionSyncSummary = {
  readonly total: number;
  readonly updated: number;
  readonly unchanged: number;
  readonly skipped: number;
  readonly failed: number;
  readonly remaining: number;
  readonly items: readonly DescriptionSyncItem[];
};

export type DescriptionSyncInput = {
  readonly framework?: PluginFrameworkSection;
  readonly overwrite?: boolean;
  readonly pluginIds?: readonly number[];
};

export type GitHubRepoInfo = {
  readonly success: boolean;
  readonly repoName: string | null;
  /** The repository's one-line GitHub description. */
  readonly description: string | null;
  /** The repository's full README Markdown, capped at the description column. */
  readonly readme: string | null;
  readonly author: string | null;
  readonly error: string | null;
};

export type PluginDependencyOptions = {
  readonly id: number;
  readonly title: string;
};

export type MarketQuery = {
  readonly q?: string;
  readonly category?: string;
  readonly framework?: PluginFrameworkSection;
  readonly limit?: number;
  readonly offset?: number;
};

export type ManagedPlugin = {
  readonly id: number;
  readonly serverId: number;
  readonly sourceType: string;
  readonly sourceKey: string;
  readonly displayName: string;
  readonly repoUrl: string | null;
  readonly marketPluginId: number | null;
  readonly frameworkKey: string | null;
  readonly installedVersion: string;
  readonly latestVersion: string | null;
  readonly autoUpdateEnabled: boolean;
  readonly lastStatus: string | null;
  readonly lastError: string | null;
  readonly lastCheckAt: string | null;
  readonly lastUpdateAt: string | null;
};

export type PluginConflict = {
  readonly ruleId: number;
  readonly pluginAId: number;
  readonly pluginBId: number;
  readonly severity: string;
  readonly reason: string;
};

export type PluginInstallStep = {
  readonly order: number;
  readonly pluginId: number;
  readonly title: string;
  readonly kind: string;
  readonly status: string;
  readonly reason: string;
};

export const CATALOG_STRATEGIES = ["skip", "update"] as const;
export type CatalogStrategy = (typeof CATALOG_STRATEGIES)[number];

export type PluginCatalogImportAction =
  | "imported"
  | "updated"
  | "skipped"
  | "failed";

export type PluginCatalogImportResult = {
  readonly index: number;
  readonly kind: "plugin" | "conflict";
  readonly name: string;
  readonly action: PluginCatalogImportAction;
  readonly pluginId: number | null;
  readonly message: string | null;
};

export type PluginCatalogImportSummary = {
  readonly total: number;
  readonly imported: number;
  readonly updated: number;
  readonly skipped: number;
  readonly failed: number;
  readonly results: readonly PluginCatalogImportResult[];
};

export type PluginCatalogBundle = {
  readonly format: "upkk-cs2-plugin-catalog";
  readonly version: number;
  readonly exported_at?: string | null;
  readonly plugins: readonly Record<string, unknown>[];
  readonly conflicts: readonly Record<string, unknown>[];
};

export type PluginCatalogImportRequest = PluginCatalogBundle & {
  readonly conflict_strategy: CatalogStrategy;
};

export const CATALOG_ACTION_TONE: Record<
  PluginCatalogImportAction,
  "ok" | "info" | "neutral" | "danger"
> = {
  imported: "ok",
  updated: "info",
  skipped: "neutral",
  failed: "danger",
};

export type GitHubReleaseAsset = {
  readonly name: string;
  readonly browserDownloadUrl: string;
  readonly size: number;
  readonly steamRuntime: string | null;
  readonly runtimeCompatibility: string;
};

export type GitHubRelease = {
  readonly id: string | null;
  readonly tagName: string;
  readonly name: string | null;
  readonly publishedAt: string | null;
  readonly prerelease: boolean;
  readonly assets: readonly GitHubReleaseAsset[];
};

export type GitHubReleases = {
  readonly repoOwner: string | null;
  readonly repoName: string | null;
  readonly releases: readonly GitHubRelease[];
};

export type GitHubArchiveFile = {
  readonly path: string;
  readonly isDir: boolean;
  readonly size: number;
};

export type GitHubArchive = {
  readonly hasAddonsDir: boolean;
  readonly rootDirs: readonly string[];
  readonly allDirs: readonly string[];
  readonly allFiles: readonly GitHubArchiveFile[];
  readonly archiveType: string | null;
};

export type GitHubArchiveMapping = {
  readonly source: string;
  readonly target: string;
};

export type GitHubInstallPlan = {
  readonly serverId: number;
  readonly repoUrl: string;
  readonly mode: string;
  readonly configPolicy: string;
  readonly planHash: string;
  readonly releaseTag: string | null;
  readonly releaseName: string | null;
  readonly assetName: string | null;
  readonly archiveSha256: string | null;
  readonly mappingRequired: boolean;
  readonly sourcePrefix: string | null;
  readonly mapping: readonly GitHubArchiveMapping[];
  readonly recipeId: number | null;
  readonly excludeDirs: readonly string[];
  readonly excludeFiles: readonly string[];
  readonly warnings: readonly string[];
  readonly hardConflicts: readonly PluginConflict[];
  readonly conflictWarnings: readonly PluginConflict[];
  readonly compatibilityUnknown: boolean;
  readonly alreadyInstalled: readonly number[];
  readonly dependencies: readonly PluginRef[];
};

/** Whether the target server actually runs the plugin's runtime. */
export type PluginFrameworkCompatibility = {
  readonly plugin: string;
  readonly installed: readonly string[];
  readonly conflicting: readonly string[];
  readonly missing: boolean;
  readonly mismatch: boolean;
};

export type PluginInstallPlan = {
  readonly serverId: number;
  readonly plugin: PluginRef;
  readonly dependencies: readonly PluginRef[];
  readonly installationOrder: readonly number[];
  readonly alreadyInstalled: readonly number[];
  readonly trackingRecordsWithoutRemoteEvidence: readonly string[];
  readonly compatibilityUnknown: readonly string[];
  readonly hardConflicts: readonly PluginConflict[];
  readonly warnings: readonly PluginConflict[];
  readonly framework: PluginFrameworkCompatibility;
  readonly steps: readonly PluginInstallStep[];
  readonly blocked: boolean;
  readonly planHash: string;
};
