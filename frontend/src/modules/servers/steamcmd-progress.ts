const PROGRESS =
  /(?:Update state \(0x[0-9a-f]+\) downloading,\s+)?progress:\s+(\d+(?:\.\d+)?)\s+\(\s*(\d+)\s*\/\s*(\d+)\s*\)/gi;

export function latestSteamcmdProgress(text: string): string | null {
  const flat = text.replace(/[\r\n]+/g, " ");
  let best: { bytes: number; line: string } | null = null;
  for (const match of flat.matchAll(PROGRESS)) {
    const bytes = Number(match[2]);
    if (!Number.isFinite(bytes)) continue;
    const line = match[0].replace(/\s+/g, " ").trim();
    if (!best || bytes >= best.bytes) {
      best = { bytes, line };
    }
  }
  return best?.line ?? null;
}
