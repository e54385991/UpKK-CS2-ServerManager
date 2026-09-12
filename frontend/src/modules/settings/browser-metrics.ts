export type BrowserNavigationMetrics = {
  readonly ttfb_ms: number | null;
  readonly dcl_ms: number | null;
  readonly load_ms: number | null;
  readonly transfer_bytes: number | null;
};

export type BrowserMetrics = {
  readonly captured_at: string;
  readonly navigation: BrowserNavigationMetrics;
  readonly fcp_ms: number | null;
  readonly cls: number | null;
  readonly resource_count: number;
  readonly resource_transfer_bytes: number;
};

type PaintLike = { readonly name: string; readonly startTime: number };
type LayoutShiftLike = {
  readonly value: number;
  readonly hadRecentInput?: boolean;
};
type ResourceLike = { readonly transferSize?: number };
type NavigationLike = {
  readonly requestStart: number;
  readonly responseStart: number;
  readonly domContentLoadedEventEnd: number;
  readonly loadEventEnd: number;
  readonly startTime: number;
  readonly transferSize?: number;
};

export function collectBrowserMetrics(
  now = new Date(),
  performanceLike: Pick<Performance, "getEntriesByType"> | null = globalThis.performance,
): BrowserMetrics {
  const captured_at = now.toISOString();
  if (performanceLike == null) {
    return {
      captured_at,
      navigation: {
        ttfb_ms: null,
        dcl_ms: null,
        load_ms: null,
        transfer_bytes: null,
      },
      fcp_ms: null,
      cls: null,
      resource_count: 0,
      resource_transfer_bytes: 0,
    };
  }
  const navigation = firstNavigation(performanceLike.getEntriesByType("navigation"));
  const paints = performanceLike.getEntriesByType("paint") as unknown as PaintLike[];
  const shifts = performanceLike.getEntriesByType("layout-shift") as unknown as LayoutShiftLike[];
  const resources = performanceLike.getEntriesByType("resource") as unknown as ResourceLike[];
  const fcp = paints.find((entry) => entry.name === "first-contentful-paint");
  return {
    captured_at,
    navigation: {
      ttfb_ms: durationMs(navigation?.responseStart, navigation?.requestStart),
      dcl_ms: durationMs(navigation?.domContentLoadedEventEnd, navigation?.startTime),
      load_ms: durationMs(navigation?.loadEventEnd, navigation?.startTime),
      transfer_bytes: navigation?.transferSize ?? null,
    },
    fcp_ms: fcp ? roundMs(fcp.startTime) : null,
    cls: cumulativeCls(shifts),
    resource_count: resources.length,
    resource_transfer_bytes: resources.reduce(
      (total, entry) => total + (entry.transferSize ?? 0),
      0,
    ),
  };
}

function firstNavigation(entries: PerformanceEntryList): NavigationLike | null {
  const first = entries[0] as NavigationLike | undefined;
  return first ?? null;
}

function durationMs(end?: number, start?: number): number | null {
  if (end == null || start == null) return null;
  return roundMs(end - start);
}

function roundMs(value: number): number {
  return Math.round(value * 10) / 10;
}

function cumulativeCls(shifts: LayoutShiftLike[]): number | null {
  if (shifts.length === 0) return null;
  const total = shifts.reduce(
    (sum, entry) => (entry.hadRecentInput ? sum : sum + entry.value),
    0,
  );
  return Math.round(total * 1000) / 1000;
}
