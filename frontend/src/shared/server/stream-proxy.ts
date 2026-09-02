/**
 * Forward a long-lived upstream stream without buffering it in Next. Browser
 * navigation and EventSource reconnects cancel the downstream reader; that is
 * an expected lifecycle event and must not become an unhandled stream error.
 */
export function pipeUnbuffered(
  body: ReadableStream<Uint8Array>,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let closed = false;
  return new ReadableStream({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          closed = true;
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (error) {
        if (closed) return;
        if (isExpectedCancellation(error)) {
          closed = true;
          try {
            controller.close();
          } catch {
            // The downstream cancellation may have closed the controller first.
          }
          return;
        }
        controller.error(error);
      }
    },
    cancel(reason) {
      closed = true;
      return reader.cancel(reason).catch(() => undefined);
    },
  });
}

function isExpectedCancellation(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  const normalized = message.toLowerCase();
  return (
    normalized.includes("abort") ||
    normalized.includes("context canceled") ||
    normalized.includes("premature close") ||
    normalized.includes("client disconnected")
  );
}
