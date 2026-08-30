export function cleanupScanStreamUrl(serverId: number): string {
  return `/cleanup-stream/servers/${serverId}/scan`;
}

export function cleanupSystemStreamUrl(serverId: number): string {
  return `/cleanup-stream/servers/${serverId}/system`;
}

export type CleanupStreamPhase = {
  readonly type: "phase";
  readonly phase?: string;
  readonly message?: string;
};

export type CleanupStreamError = {
  readonly type: "error";
  readonly message?: string;
};

export function openCleanupEventSource(
  url: string,
  handlers: {
    readonly onPhase?: (message: string) => void;
    readonly onEvent?: (type: string, data: Record<string, unknown>) => void;
    readonly onDone: (data: Record<string, unknown>) => void;
    readonly onError: (message: string) => void;
  },
): () => void {
  const source = new EventSource(url);
  let settled = false;

  function finish(work: () => void) {
    if (settled) return;
    settled = true;
    source.close();
    work();
  }

  function read(event: MessageEvent): Record<string, unknown> {
    try {
      return JSON.parse(String(event.data || "{}")) as Record<string, unknown>;
    } catch {
      return {};
    }
  }

  source.addEventListener("phase", (event) => {
    const data = read(event as MessageEvent);
    const message = typeof data.message === "string" ? data.message : "";
    if (message) handlers.onPhase?.(message);
    handlers.onEvent?.("phase", data);
  });
  source.addEventListener("batch", (event) => {
    handlers.onEvent?.("batch", read(event as MessageEvent));
  });
  source.addEventListener("target", (event) => {
    handlers.onEvent?.("target", read(event as MessageEvent));
  });
  source.addEventListener("done", (event) => {
    finish(() => handlers.onDone(read(event as MessageEvent)));
  });
  source.addEventListener("error", (event) => {
    if (event instanceof MessageEvent && event.data) {
      const data = read(event);
      finish(() =>
        handlers.onError(typeof data.message === "string" ? data.message : "Cleanup stream failed"),
      );
      return;
    }
    // Close immediately so EventSource cannot reconnect and start another SSH scan.
    finish(() => handlers.onError("Cleanup stream closed"));
  });

  return () => {
    settled = true;
    source.close();
  };
}
