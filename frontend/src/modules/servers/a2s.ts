export function parseA2SVersion(version: string | null | undefined): string | null {
  if (!version) return null;
  const match = version.match(/(\d+\.\d+\.\d+\.\d+)/);
  return match?.[1] ?? version;
}

export function isA2SVersionOutdated(
  serverVersion: string | null | undefined,
  steamVersion: string | null | undefined,
): boolean {
  const parsed = parseA2SVersion(serverVersion);
  return Boolean(parsed && steamVersion && parsed !== steamVersion);
}
