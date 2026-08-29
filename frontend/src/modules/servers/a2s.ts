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

export function formatA2SDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remain = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}
