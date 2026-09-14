export type ProductionRouteMetrics = {
  readonly route: string;
  readonly locale: string;
  readonly ttfb_ms: number | null;
  readonly fcp_ms: number | null;
  readonly lcp_ms: number | null;
  readonly cls: number | null;
  readonly inp_ms: number | null;
  readonly long_task_count: number;
  readonly long_task_total_ms: number;
  readonly html_bytes: number | null;
  readonly rsc_bytes: number;
  readonly js_transfer_bytes: number;
  readonly critical_content_ms: number | null;
};

export function protocolFromEnv(env: { [key: string]: string | undefined } = process.env) {
  return {
    warmup: Number(env.PERF_WARMUP ?? 1),
    measure: Number(env.PERF_MEASURE ?? 2),
    rounds: Number(env.PERF_ROUNDS ?? 1),
  };
}

export function summarize(samples: ProductionRouteMetrics[]) {
  const numbers = (read: (row: ProductionRouteMetrics) => number | null) =>
    samples
      .map(read)
      .filter((value): value is number => value != null)
      .sort((left, right) => left - right);
  const pick = (values: number[], percent: number) => {
    if (values.length === 0) return null;
    const rank = Math.max(1, Math.ceil((percent / 100) * values.length));
    return values[Math.min(values.length, rank) - 1] ?? null;
  };
  const critical = numbers((row) => row.critical_content_ms);
  const cls = numbers((row) => row.cls);
  return {
    count: samples.length,
    critical_content_ms: { p50: pick(critical, 50), p95: pick(critical, 95) },
    cls: { p50: pick(cls, 50), p95: pick(cls, 95) },
    html_bytes: pick(numbers((row) => row.html_bytes), 50),
    rsc_bytes: pick(numbers((row) => row.rsc_bytes), 50),
    js_transfer_bytes: pick(numbers((row) => row.js_transfer_bytes), 50),
  };
}
