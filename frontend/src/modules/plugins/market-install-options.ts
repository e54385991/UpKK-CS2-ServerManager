export function pluginTrackedOnServer(
  installedMarketPluginIds: readonly number[],
  pluginId: number,
): boolean {
  return installedMarketPluginIds.includes(pluginId);
}

export function installOptionDefaults(existsOnServer: boolean): {
  readonly upgradeMode: boolean;
  readonly installDependencies: boolean;
} {
  return existsOnServer
    ? { upgradeMode: true, installDependencies: false }
    : { upgradeMode: false, installDependencies: true };
}

export function pickDefaultAssetIndex(
  assets: readonly { readonly runtimeCompatibility: string }[],
): number | null {
  const recommended = assets.flatMap((asset, index) =>
    asset.runtimeCompatibility === "recommended" ? [index] : [],
  );
  const hasPairedRuntime = assets.some((asset) =>
    ["recommended", "alternative", "unknown"].includes(asset.runtimeCompatibility),
  );
  if (recommended.length === 1) return recommended[0] ?? null;
  if (!hasPairedRuntime && assets.length > 0) return 0;
  return null;
}

export function formatArchiveSize(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(1)} ${units[index]}`;
}

export function toggleExclusion(
  values: readonly string[],
  item: string,
): string[] {
  return values.includes(item)
    ? values.filter((value) => value !== item)
    : [...values, item];
}
