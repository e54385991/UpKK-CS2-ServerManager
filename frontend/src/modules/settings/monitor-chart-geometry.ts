export type ChartPoint = {
  readonly ts: string;
  readonly value: number | null;
};

export function chartLinePaths(
  series: readonly ChartPoint[],
  x: (index: number) => number,
  y: (value: number) => number,
): Array<{ d: string; area: string | null }> {
  const paths: Array<{ d: string; area: string | null }> = [];
  let d = "";
  let start = 0;
  let open = false;
  for (let index = 0; index < series.length; index += 1) {
    const value = series[index]?.value ?? null;
    if (value == null) {
      if (open) {
        paths.push({ d, area: `${d} L${x(index > 0 ? index - 1 : 0)} ${y(0)} L${x(start)} ${y(0)} Z` });
        open = false;
        d = "";
      }
      continue;
    }
    if (!open) {
      start = index;
      d = `M${x(index)} ${y(value)}`;
      open = true;
    } else {
      d += ` L${x(index)} ${y(value)}`;
    }
  }
  if (open) {
    const last = series.length - 1;
    paths.push({ d, area: `${d} L${x(last)} ${y(0)} L${x(start)} ${y(0)} Z` });
  }
  return paths;
}

export function nearestChartIndex(series: readonly ChartPoint[], markerTs: string | null | undefined): number {
  if (!markerTs || series.length === 0) return -1;
  const target = Date.parse(markerTs);
  if (Number.isNaN(target)) return -1;
  let best = 0;
  let bestDist = Number.POSITIVE_INFINITY;
  for (let index = 0; index < series.length; index += 1) {
    const dist = Math.abs(Date.parse(series[index]!.ts) - target);
    if (dist < bestDist) {
      best = index;
      bestDist = dist;
    }
  }
  return best;
}

export function sparkSegments(values: readonly (number | null)[]): string[] {
  const numeric = values.filter((value): value is number => value != null);
  if (numeric.length === 0) return [];
  const max = Math.max(...numeric, 1);
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value == null) {
      if (current.length > 0) {
        segments.push(current.join(" "));
        current = [];
      }
      return;
    }
    current.push(`${index},${(1 - value / max) * 32}`);
  });
  if (current.length > 0) segments.push(current.join(" "));
  return segments;
}

export function formatChartClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function formatChartNumber(value: number): string {
  if (Math.abs(value) >= 100) return `${Math.round(value)}`;
  return value.toFixed(value >= 10 ? 1 : 2);
}

export function chartIndexFromLocalX(
  localX: number,
  count: number,
  width: number,
  padLeft: number,
  padRight: number,
): number {
  if (count <= 0) return -1;
  if (count === 1) return 0;
  const innerW = Math.max(width - padLeft - padRight, 1);
  const t = (localX - padLeft) / innerW;
  return Math.round(Math.max(0, Math.min(1, t)) * (count - 1));
}

export function nearestNumericChartIndex(series: readonly ChartPoint[], index: number): number {
  if (series.length === 0) return -1;
  const clamped = Math.max(0, Math.min(series.length - 1, index));
  if (series[clamped]?.value != null) return clamped;
  for (let distance = 1; distance < series.length; distance += 1) {
    const left = clamped - distance;
    const right = clamped + distance;
    if (left >= 0 && series[left]?.value != null) return left;
    if (right < series.length && series[right]?.value != null) return right;
  }
  return -1;
}
