import type { PanelPerformanceSnapshotDto } from "@/shared/api/types";
import type { BrowserMetrics } from "@/modules/settings/browser-metrics";

export type PanelPerformanceExport = PanelPerformanceSnapshotDto & {
  readonly browser?: BrowserMetrics;
};

export function buildPerformanceExport(
  snapshot: PanelPerformanceSnapshotDto,
  browser: BrowserMetrics,
): PanelPerformanceExport {
  return {
    ...snapshot,
    priority: [...snapshot.priority, "browser"],
    browser,
  };
}

export function performanceExportFilename(capturedAt: string, now = new Date()): string {
  const stamp = (capturedAt || now.toISOString()).replace(/[:.]/g, "-").slice(0, 19);
  return `cs2-panel-performance-${stamp}.json`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KiB", "MiB", "GiB"] as const;
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

export function formatMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value >= 100) return `${Math.round(value)} ms`;
  return `${value.toFixed(1)} ms`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(value >= 0.1 ? 1 : 2)}%`;
}
