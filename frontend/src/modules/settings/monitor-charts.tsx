"use client";

import { useId, useMemo, useState } from "react";
import { cn } from "@/shared/lib/cn";

export type ChartPoint = {
  readonly ts: string;
  readonly value: number | null;
};

const WIDTH = 640;
const HEIGHT = 196;
const PAD = { top: 16, right: 12, bottom: 28, left: 44 };

export function MonitorChart({
  title,
  unit,
  series,
  color = "var(--color-primary)",
  thresholds = [],
  hint,
  testId,
}: {
  title: string;
  unit: string;
  series: readonly ChartPoint[];
  color?: string;
  thresholds?: readonly { readonly value: number; readonly label: string; readonly tone: "warn" | "danger" }[];
  hint?: string;
  testId?: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const gradientId = useId();
  const values = series.map((point) => point.value);
  const numeric = values.filter((value): value is number => value != null);
  const maxThreshold = thresholds.reduce((highest, item) => Math.max(highest, item.value), 0);
  const maxValue = Math.max(numeric.length > 0 ? Math.max(...numeric) : 0, maxThreshold, 1);
  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const paths = useMemo(() => {
    const xAt = (index: number) =>
      PAD.left + (series.length <= 1 ? innerW / 2 : (index / (series.length - 1)) * innerW);
    const yAt = (value: number) => PAD.top + innerH - (value / maxValue) * innerH;
    return linePaths(series, xAt, yAt);
  }, [innerH, innerW, maxValue, series]);
  const x = (index: number) =>
    PAD.left + (series.length <= 1 ? innerW / 2 : (index / (series.length - 1)) * innerW);
  const y = (value: number) => PAD.top + innerH - (value / maxValue) * innerH;
  const activePoint = active != null ? series[active] : null;

  function move(next: number) {
    if (series.length === 0) return;
    setActive(Math.max(0, Math.min(series.length - 1, next)));
  }

  return (
    <section
      data-testid={testId}
      className="rounded-lg border border-line bg-surface-raised/40 p-4"
    >
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <span className="text-xs text-fg-subtle">{unit}</span>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={title}
        tabIndex={0}
        className="h-48 w-full outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
        onMouseLeave={() => setActive(null)}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") move((active ?? -1) + 1);
          if (event.key === "ArrowLeft") move((active ?? series.length) - 1);
        }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.28" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {thresholds.map((line) => (
          <g key={line.label}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(line.value)}
              y2={y(line.value)}
              stroke={line.tone === "danger" ? "var(--color-danger)" : "var(--color-warn)"}
              strokeDasharray="4 4"
              strokeWidth="1"
            />
            <text x={PAD.left + 4} y={y(line.value) - 4} className="fill-fg-subtle" fontSize="10">
              {line.label}
            </text>
          </g>
        ))}
        {paths.map((path) => (
          <g key={path.d}>
            {path.area ? <path d={path.area} fill={`url(#${gradientId})`} /> : null}
            <path d={path.d} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" />
          </g>
        ))}
        {series.map((point, index) =>
          point.value == null ? null : (
            <circle
              key={point.ts + index}
              cx={x(index)}
              cy={y(point.value)}
              r={active === index ? 5 : 0}
              fill={color}
              onMouseEnter={() => setActive(index)}
            />
          ),
        )}
        <text x={PAD.left} y={HEIGHT - 8} className="fill-fg-subtle" fontSize="10">
          {series[0] ? formatClock(series[0].ts) : ""}
        </text>
        <text x={WIDTH - PAD.right} y={HEIGHT - 8} textAnchor="end" className="fill-fg-subtle" fontSize="10">
          {series.at(-1) ? formatClock(series.at(-1)!.ts) : ""}
        </text>
      </svg>
      <p className="mt-1 min-h-5 text-xs text-fg-muted" role="status">
        {activePoint?.value != null
          ? `${formatClock(activePoint.ts)} · ${formatNumber(activePoint.value)} ${unit}`
          : hint}
      </p>
    </section>
  );
}

export function Sparkline({
  values,
  className,
  tone = "ok",
}: {
  values: readonly (number | null)[];
  className?: string;
  tone?: "ok" | "warn" | "danger";
}) {
  const numeric = values.filter((value): value is number => value != null);
  if (numeric.length === 0) {
    return <div className={cn("h-10 w-full rounded-sm bg-surface-overlay", className)} />;
  }
  const max = Math.max(...numeric, 1);
  const width = Math.max(values.length - 1, 1);
  const points = values
    .map((value, index) => (value == null ? null : `${index},${(1 - value / max) * 32}`))
    .filter((item): item is string => item != null)
    .join(" ");
  const stroke =
    tone === "danger" ? "var(--color-danger)" : tone === "warn" ? "var(--color-warn)" : "var(--color-primary)";
  return (
    <svg viewBox={`0 0 ${width} 32`} preserveAspectRatio="none" className={cn("h-10 w-full", className)}>
      <polyline fill="none" stroke={stroke} strokeWidth="1.8" points={points} />
    </svg>
  );
}

function linePaths(
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

function formatClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function formatNumber(value: number): string {
  if (Math.abs(value) >= 100) return `${Math.round(value)}`;
  return value.toFixed(value >= 10 ? 1 : 2);
}
