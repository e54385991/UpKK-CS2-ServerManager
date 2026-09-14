export const DISPLAY_UPDATE_INTERVAL_MS = 50;

export type RenderCoalesceHost = {
  schedule: (fn: () => void, ms: number) => number;
  cancel: (id: number) => void;
};

export const browserRenderCoalesceHost: RenderCoalesceHost = {
  schedule: (fn, ms) => window.setTimeout(fn, ms),
  cancel: (id) => window.clearTimeout(id),
};

export type RenderCoalescer<T> = {
  push(item: T, options?: { immediate?: boolean }): void;
  flush(): void;
  dispose(): void;
};

export function createRenderCoalescer<T>(
  apply: (batch: readonly T[]) => void,
  intervalMs = DISPLAY_UPDATE_INTERVAL_MS,
  host: RenderCoalesceHost = browserRenderCoalesceHost,
): RenderCoalescer<T> {
  let buffer: T[] = [];
  let timer = 0;

  const drain = () => {
    timer = 0;
    if (buffer.length === 0) return;
    const batch = buffer;
    buffer = [];
    apply(batch);
  };

  return {
    push(item, options) {
      buffer.push(item);
      if (options?.immediate) {
        if (timer) {
          host.cancel(timer);
          timer = 0;
        }
        drain();
        return;
      }
      if (!timer) timer = host.schedule(drain, intervalMs);
    },
    flush() {
      if (timer) {
        host.cancel(timer);
        timer = 0;
      }
      drain();
    },
    dispose() {
      if (timer) {
        host.cancel(timer);
        timer = 0;
      }
      buffer = [];
    },
  };
}

export type TextDisplayBuffer = {
  append(delta: string): void;
  flush(): void;
  dispose(): void;
};

export function createTextDisplayBuffer(
  apply: (chunk: string) => void,
  intervalMs = DISPLAY_UPDATE_INTERVAL_MS,
  host: RenderCoalesceHost = browserRenderCoalesceHost,
): TextDisplayBuffer {
  let pending = "";
  let timer = 0;

  const drain = () => {
    timer = 0;
    if (!pending) return;
    const chunk = pending;
    pending = "";
    apply(chunk);
  };

  return {
    append(delta) {
      if (!delta) return;
      pending += delta;
      if (!timer) timer = host.schedule(drain, intervalMs);
    },
    flush() {
      if (timer) {
        host.cancel(timer);
        timer = 0;
      }
      drain();
    },
    dispose() {
      if (timer) {
        host.cancel(timer);
        timer = 0;
      }
      pending = "";
    },
  };
}

export function isTerminalOperationEventType(type: string): boolean {
  return type === "operation_completed" || type === "operation_failed";
}
