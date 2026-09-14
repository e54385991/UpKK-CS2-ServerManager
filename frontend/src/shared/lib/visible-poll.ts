export type VisiblePollHost = {
  hidden: () => boolean;
  now: () => number;
  onVisibilityChange: (listener: () => void) => () => void;
  schedule: (fn: () => void, ms: number) => number;
  cancel: (id: number) => void;
};

export function browserVisiblePollHost(): VisiblePollHost {
  return {
    hidden: () => document.hidden,
    now: () => Date.now(),
    onVisibilityChange: (listener) => {
      document.addEventListener("visibilitychange", listener);
      return () => document.removeEventListener("visibilitychange", listener);
    },
    schedule: (fn, ms) => window.setTimeout(fn, ms),
    cancel: (id) => window.clearTimeout(id),
  };
}

export function snapshotIsFresh(lastAt: number, now: number, windowMs: number): boolean {
  return lastAt > 0 && now - lastAt < windowMs;
}

export function subscribeVisiblePoll<T>(input: {
  intervalMs: number | (() => number);
  pull: (signal: AbortSignal) => Promise<T>;
  onResult: (value: T) => void;
  shouldPull?: () => boolean;
  host?: VisiblePollHost;
}): { stop: () => void; refresh: () => void } {
  const host = input.host ?? browserVisiblePollHost();
  let cancelled = false;
  let generation = 0;
  let inflight = false;
  let timer = 0;
  let abort = new AbortController();

  const interval = () =>
    typeof input.intervalMs === "function" ? input.intervalMs() : input.intervalMs;

  const clearTimer = () => {
    host.cancel(timer);
    timer = 0;
  };

  const abortInflight = () => {
    abort.abort();
    abort = new AbortController();
    inflight = false;
  };

  const arm = () => {
    clearTimer();
    if (cancelled || host.hidden()) return;
    timer = host.schedule(() => {
      void run("timer");
    }, interval());
  };

  const run = async (reason: "start" | "timer" | "visible" | "manual") => {
    if (cancelled || host.hidden() || inflight) return;
    if (reason === "timer" && input.shouldPull && !input.shouldPull()) {
      arm();
      return;
    }
    inflight = true;
    const gen = generation;
    const controller = abort;
    arm();
    try {
      const result = await input.pull(controller.signal);
      if (cancelled || gen !== generation || controller.signal.aborted) return;
      input.onResult(result);
    } catch {
      /* aborted or transport errors are ignored; the next arm retries */
    } finally {
      if (gen === generation) inflight = false;
      if (!cancelled && !host.hidden() && timer === 0) arm();
    }
  };

  const refresh = () => {
    void run("manual");
  };

  const stopVisibility = host.onVisibilityChange(() => {
    if (cancelled) return;
    if (host.hidden()) {
      abortInflight();
      clearTimer();
      return;
    }
    void run("visible");
  });

  void run("start");

  return {
    refresh,
    stop: () => {
      cancelled = true;
      generation += 1;
      abortInflight();
      clearTimer();
      stopVisibility();
    },
  };
}

type SharedPollEntry = {
  listeners: Set<(value: never) => void>;
  stop: () => void;
  refresh: () => void;
};

const sharedPolls = new Map<string, SharedPollEntry>();

export function resetSharedVisiblePollsForTests(): void {
  for (const entry of sharedPolls.values()) entry.stop();
  sharedPolls.clear();
}

export function refreshSharedVisiblePoll(key: string): void {
  sharedPolls.get(key)?.refresh();
}

export function subscribeSharedVisiblePoll<T>(input: {
  key: string;
  intervalMs: number | (() => number);
  pull: (signal: AbortSignal) => Promise<T>;
  onResult: (value: T) => void;
  shouldPull?: () => boolean;
  host?: VisiblePollHost;
}): () => void {
  const existing = sharedPolls.get(input.key);
  if (existing) {
    existing.listeners.add(input.onResult as (value: never) => void);
    return () => detachSharedListener(input.key, input.onResult);
  }
  const listeners = new Set<(value: never) => void>();
  const poll = subscribeVisiblePoll({
    intervalMs: input.intervalMs,
    pull: input.pull,
    shouldPull: input.shouldPull,
    host: input.host,
    onResult: (value: T) => {
      for (const listener of [...listeners]) {
        (listener as (value: T) => void)(value);
      }
    },
  });
  listeners.add(input.onResult as (value: never) => void);
  sharedPolls.set(input.key, { listeners, stop: poll.stop, refresh: poll.refresh });
  return () => detachSharedListener(input.key, input.onResult);
}

function detachSharedListener<T>(key: string, listener: (value: T) => void): void {
  const entry = sharedPolls.get(key);
  if (!entry) return;
  entry.listeners.delete(listener as (value: never) => void);
  if (entry.listeners.size > 0) return;
  queueMicrotask(() => {
    const current = sharedPolls.get(key);
    if (current !== entry || current.listeners.size > 0) return;
    current.stop();
    sharedPolls.delete(key);
  });
}
