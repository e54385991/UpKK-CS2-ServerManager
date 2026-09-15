export type ProductionSurface = {
  readonly name: string;
  readonly path: string;
  readonly actor: string | null;
};

export const PRODUCTION_SURFACES: readonly ProductionSurface[] = [
  { name: "login", path: "/login", actor: null },
  { name: "overview", path: "/overview", actor: "admin" },
  { name: "servers", path: "/servers", actor: "admin" },
  { name: "activity-tray", path: "/overview", actor: "admin" },
  { name: "plugins-install", path: "/plugins/1", actor: "admin" },
  { name: "assistant", path: "/assistant?conversation=conversation-1", actor: "admin" },
  { name: "files", path: "/servers/1/files", actor: "admin" },
];

export const PRODUCTION_WIDTHS = [390, 1440] as const;
export const PRODUCTION_HEIGHT = 844;

function csv(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function surfacesFromEnv(
  env: { [key: string]: string | undefined } = process.env,
): ProductionSurface[] {
  const names = csv(env.PERF_BROWSER_ROUTES);
  if (names.length === 0) {
    return [...PRODUCTION_SURFACES];
  }
  const byName = new Map(PRODUCTION_SURFACES.map((item) => [item.name, item]));
  const missing = names.filter((name) => !byName.has(name));
  if (missing.length > 0) {
    throw new Error(`unknown PERF_BROWSER_ROUTES: ${missing.join(", ")}`);
  }
  return names.map((name) => byName.get(name)!);
}

export function widthsFromEnv(
  env: { [key: string]: string | undefined } = process.env,
): number[] {
  const raw = csv(env.PERF_WIDTHS);
  if (raw.length === 0) {
    return [...PRODUCTION_WIDTHS];
  }
  const allowed = new Set<number>(PRODUCTION_WIDTHS);
  const widths = raw.map((item) => Number(item));
  const unknown = widths.filter((width) => !allowed.has(width));
  if (unknown.length > 0) {
    throw new Error(`unknown PERF_WIDTHS: ${unknown.join(", ")}`);
  }
  return widths;
}
