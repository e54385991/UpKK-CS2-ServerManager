export const OPERATION_INBOX_LOCK = "upkk-operation-inbox-sse";

export type EventSourceLike = {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener: (
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) => void;
  close: () => void;
};

export type VisibleEventSourceHost = {
  hidden: () => boolean;
  onVisibilityChange: (listener: () => void) => () => void;
  eventSource: (url: string) => EventSourceLike;
  requestLock?: (
    name: string,
    options: { signal: AbortSignal },
    run: () => Promise<void>,
  ) => Promise<unknown>;
  delayMs: (attempt: number) => number;
  schedule: (fn: () => void, ms: number) => number;
  cancel: (id: number) => void;
};

export function browserVisibleEventSourceHost(): VisibleEventSourceHost {
  const locks = navigator.locks;
  return {
    hidden: () => document.hidden,
    onVisibilityChange: (listener) => {
      document.addEventListener("visibilitychange", listener);
      return () => document.removeEventListener("visibilitychange", listener);
    },
    eventSource: (url) => new EventSource(url),
    requestLock: locks
      ? (name, options, run) => locks.request(name, options, run)
      : undefined,
    delayMs: (attempt) => Math.min(8000, 400 * 2 ** Math.min(Math.max(attempt, 0), 4)),
    schedule: (fn, ms) => window.setTimeout(fn, ms),
    cancel: (id) => window.clearTimeout(id),
  };
}

export function subscribeVisibleEventSource(input: {
  url: string | (() => string);
  onData: (data: string) => void;
  eventTypes?: readonly string[];
  lockName?: string;
  shouldReconnect?: () => boolean;
  onOpen?: () => void;
  onUnavailable?: () => void;
  host?: VisibleEventSourceHost;
}): () => void {
  const host = input.host ?? browserVisibleEventSourceHost();
  let cancelled = false;
  let source: EventSourceLike | null = null;
  let attempt = 0;
  let timer = 0;
  let lockAbort = new AbortController();

  const closeSource = () => {
    source?.close();
    source = null;
  };

  const attach = () => {
    if (cancelled || host.hidden()) return;
    closeSource();
    const href = typeof input.url === "function" ? input.url() : input.url;
    const next = host.eventSource(href);
    source = next;
    const handle = (event: MessageEvent<string>) => {
      if (typeof event.data === "string" && event.data) input.onData(event.data);
    };
    next.onmessage = handle;
    for (const type of input.eventTypes ?? []) {
      next.addEventListener(type, handle);
    }
    next.onopen = () => {
      attempt = 0;
      input.onOpen?.();
    };
    next.onerror = () => {
      closeSource();
      if (cancelled || host.hidden()) return;
      if (input.shouldReconnect && !input.shouldReconnect()) {
        input.onUnavailable?.();
        return;
      }
      const wait = host.delayMs(attempt);
      attempt += 1;
      timer = host.schedule(attach, wait);
    };
  };

  const stopLock = () => {
    lockAbort.abort();
    lockAbort = new AbortController();
  };

  const start = () => {
    host.cancel(timer);
    stopLock();
    closeSource();
    if (cancelled || host.hidden()) return;
    if (input.lockName && host.requestLock) {
      void host
        .requestLock(input.lockName, { signal: lockAbort.signal }, async () => {
          attach();
          await new Promise<void>((resolve) => {
            const done = () => resolve();
            lockAbort.signal.addEventListener("abort", done, { once: true });
            if (lockAbort.signal.aborted) done();
          });
        })
        .catch(() => {
          /* lock aborted when hidden or unmounted */
        });
      return;
    }
    attach();
  };

  const stopVisibility = host.onVisibilityChange(start);
  start();

  return () => {
    cancelled = true;
    host.cancel(timer);
    stopVisibility();
    stopLock();
    closeSource();
  };
}
