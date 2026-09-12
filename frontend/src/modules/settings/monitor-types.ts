export const MONITOR_RANGES = ["15m", "1h", "6h", "24h"] as const;
export type MonitorRange = (typeof MONITOR_RANGES)[number];

export const MONITOR_VIEWS = ["overview", "requests", "resources", "tasks"] as const;
export type MonitorView = (typeof MONITOR_VIEWS)[number];

export type MonitorStatus = {
  readonly enabled: boolean;
  readonly running: boolean;
  readonly stale: boolean;
  readonly history_available: boolean;
  readonly history_error: string | null;
  readonly config_sync_error: string | null;
  readonly last_sample_at: string | null;
  readonly stopped_at: string | null;
  readonly instance_id: string;
  readonly sample_interval_seconds: number;
  readonly dropped_error_details: number;
  readonly truncated_summaries: number;
  readonly collection_ms: number | null;
  readonly collecting: boolean;
};

export type MonitorSeriesPoint = {
  readonly ts: string;
  readonly request_count: number;
  readonly requests_per_minute: number;
  readonly status_5xx: number;
  readonly error_rate: number;
  readonly latency_p50_ms: number | null;
  readonly latency_p95_ms: number | null;
  readonly latency_p99_ms: number | null;
  readonly latency_max_ms: number | null;
  readonly latency_partial: boolean;
  readonly cpu_percent: number | null;
  readonly rss_bytes: number | null;
  readonly rss_peak_bytes: number | null;
  readonly loop_lag_p95_ms: number | null;
  readonly db_checked_out: number | null;
  readonly db_capacity: number | null;
  readonly redis_ping_ms: number | null;
  readonly redis_connected: boolean | null;
  readonly queue_running: number;
  readonly queue_queued: number;
  readonly oldest_queue_ms: number | null;
  readonly failed_tasks: number;
  readonly unhandled_exceptions: number;
  readonly fd_open: number | null;
  readonly fd_limit: number | null;
};

export type MonitorAlert = {
  readonly id: string;
  readonly severity: "watch" | "critical";
  readonly metric: string;
  readonly title: string;
  readonly detail: string;
  readonly guidance: string;
  readonly threshold: string;
  readonly value: number | null;
  readonly since: string | null;
};

export type MonitorFrame = {
  readonly file: string;
  readonly function: string;
  readonly line: number;
};

export type MonitorErrorGroup = {
  readonly source: string | null;
  readonly error_code: string | null;
  readonly severity: string | null;
  readonly exception_type: string | null;
  readonly route: string | null;
  readonly count: number;
  readonly first_ts: string | null;
  readonly last_ts: string | null;
  readonly summary: string;
  readonly request_id: string | null;
  readonly operation_id: string | null;
  readonly frames: readonly MonitorFrame[];
};

export type MonitorErrorDetail = {
  readonly id: string;
  readonly ts: string;
  readonly source: string;
  readonly severity: string;
  readonly error_code: string;
  readonly exception_type: string;
  readonly summary: string;
  readonly truncated: boolean;
  readonly route: string | null;
  readonly operation_id: string | null;
  readonly request_id: string | null;
  readonly frames: readonly MonitorFrame[];
};

export type MonitorInstance = {
  readonly instance_id: string;
  readonly last_seen_at: string;
  readonly current: boolean;
};

export type MonitorIntegrity = {
  readonly sample_interval_seconds: number;
  readonly display_bucket_seconds: number;
  readonly point_count: number;
  readonly gap: boolean;
  readonly partial_latency: boolean;
};

export type MonitorSnapshot = {
  readonly captured_at: string;
  readonly requests: {
    readonly sample_count: number;
    readonly requests_per_minute: number;
    readonly status_2xx: number;
    readonly status_4xx: number;
    readonly status_5xx: number;
    readonly error_rate: number;
    readonly latency_ms: { readonly p50: number; readonly p95: number; readonly p99: number; readonly max: number };
    readonly slow_request_count: number;
    readonly slow_request_threshold_ms: number;
    readonly by_route: readonly {
      readonly method: string;
      readonly route: string;
      readonly count: number;
      readonly error_count: number;
      readonly latency_ms: { readonly p95: number };
    }[];
    readonly in_flight?: number;
    readonly status_401?: number;
    readonly status_403?: number;
    readonly status_404?: number;
    readonly status_409?: number;
    readonly status_422?: number;
    readonly status_429?: number;
    readonly unhandled_exceptions?: number;
    readonly stream_errors?: number;
    readonly cancellations?: number;
  };
  readonly process: {
    readonly pid: number;
    readonly uptime_seconds: number;
    readonly rss_bytes: number | null;
    readonly cpu_percent: number | null;
    readonly threads: number;
    readonly asyncio_tasks: number | null;
    readonly event_loop_lag_ms: number | null;
    readonly event_loop_lag_p95_ms?: number | null;
    readonly fd_open?: number | null;
    readonly fd_limit?: number | null;
  };
  readonly database: {
    readonly pool_size: number;
    readonly max_overflow: number;
    readonly checked_out: number | null;
    readonly overflow: number | null;
    readonly capacity?: number;
    readonly invalidations?: number;
    readonly slow_executions?: number;
    readonly errors?: number;
    readonly execute_p95_ms?: number | null;
  };
  readonly redis: {
    readonly connected: boolean;
    readonly ping_ms: number | null;
    readonly failures?: number;
    readonly timeouts?: number;
    readonly monitor_ops?: number;
    readonly op_p95_ms?: number | null;
  };
  readonly ssh_pool: {
    readonly connections: number;
    readonly in_use: number;
    readonly idle: number;
    readonly leases: number;
    readonly reuses?: number;
    readonly auth_failures?: number;
    readonly timeouts?: number;
    readonly reconnect_attempts?: number;
    readonly reconnect_failures?: number;
    readonly connect_p95_ms?: number | null;
  };
  readonly limiters: readonly {
    readonly name: string;
    readonly borrowers: number;
    readonly executing?: number;
    readonly waiting?: number;
    readonly wait_p95_ms?: number | null;
  }[];
  readonly operations: {
    readonly running: number;
    readonly queued: number;
    readonly oldest_queue_ms?: number | null;
    readonly submitted?: number;
    readonly succeeded?: number;
    readonly failed?: number;
    readonly cancelled?: number;
  };
  readonly outbound_http?: readonly {
    readonly group: string;
    readonly calls: number;
    readonly timeouts: number;
    readonly network_errors: number;
    readonly status_429: number;
    readonly status_5xx: number;
    readonly retries: number;
    readonly p95_ms: number | null;
  }[];
  readonly runtime?: {
    readonly version: string;
    readonly python: string;
    readonly git_sha: string;
    readonly worker_pid: number;
  };
};

export type PanelMonitorView = {
  readonly range: MonitorRange;
  readonly instance_id: string;
  readonly instances: readonly MonitorInstance[];
  readonly status: MonitorStatus;
  readonly snapshot: MonitorSnapshot | null;
  readonly series: readonly MonitorSeriesPoint[];
  readonly alerts: readonly MonitorAlert[];
  readonly error_groups: readonly MonitorErrorGroup[];
  readonly integrity: MonitorIntegrity;
};

export type PanelErrorListView = {
  readonly items: readonly MonitorErrorDetail[];
  readonly next_cursor: string | null;
  readonly dropped: number;
  readonly truncated: boolean;
};
