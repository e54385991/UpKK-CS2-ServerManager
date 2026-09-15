export type TokenUsage = {
  readonly input: number;
  readonly output: number;
  readonly total: number;
  /** Input tokens the upstream prompt cache served; already inside `input`. */
  readonly cached: number;
  readonly estimated: boolean;
};

export const EMPTY_TOKEN_USAGE: TokenUsage = {
  input: 0,
  output: 0,
  total: 0,
  cached: 0,
  estimated: true,
};

export function tokenCount(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.floor(value));
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return 0;
}

export const TERMINAL_RUN = new Set([
  "completed",
  "failed",
  "interrupted",
  "expired",
  "cancelled",
]);
