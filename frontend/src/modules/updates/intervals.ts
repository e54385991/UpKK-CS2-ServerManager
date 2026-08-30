/** Same options as the legacy configuration tab (`update_check_interval_hours`). */
export const GAME_UPDATE_INTERVALS = [
  { value: 0.0167, key: "1m" },
  { value: 0.0333, key: "2m" },
  { value: 0.05, key: "3m" },
  { value: 0.0833, key: "5m" },
  { value: 0.1667, key: "10m" },
  { value: 0.25, key: "15m" },
  { value: 0.5, key: "30m" },
  { value: 1, key: "1h" },
  { value: 2, key: "2h" },
  { value: 3, key: "3h" },
  { value: 4, key: "4h" },
  { value: 6, key: "6h" },
  { value: 8, key: "8h" },
  { value: 12, key: "12h" },
  { value: 24, key: "24h" },
] as const;

export const GAME_UPDATE_MINUTE_INTERVALS = GAME_UPDATE_INTERVALS.filter(
  (item) => item.value < 1,
);
export const GAME_UPDATE_HOUR_INTERVALS = GAME_UPDATE_INTERVALS.filter(
  (item) => item.value >= 1,
);

export const PLUGIN_UPDATE_INTERVAL_MIN = 0.0167;
export const PLUGIN_UPDATE_INTERVAL_MAX = 24;

const INTERVAL_EPS = 0.00005;

export function matchGameInterval(hours: number): number | null {
  for (const item of GAME_UPDATE_INTERVALS) {
    if (Math.abs(hours - item.value) < INTERVAL_EPS) return item.value;
  }
  return null;
}

export function clampPluginInterval(hours: number, fallback: number): number {
  if (!Number.isFinite(hours)) return fallback;
  return Math.min(
    PLUGIN_UPDATE_INTERVAL_MAX,
    Math.max(PLUGIN_UPDATE_INTERVAL_MIN, hours),
  );
}

export function pluginUpdateProgressPercent(current: number, total: number, state: string): number {
  if (!total) return state === "completed" ? 100 : 0;
  return Math.min(100, Math.round((current / total) * 100));
}
