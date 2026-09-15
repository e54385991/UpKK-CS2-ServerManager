"""Per-connection inbox SSE frames. Keep-alive and event names stay unchanged."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from services.operations.inbox_equal import inbox_payloads_equal
from services.operations.inbox_types import InboxPayload

KEEP_ALIVE_IDLE_TICKS = 15
EncodeInbox = Callable[[InboxPayload], str]


@dataclass(slots=True)
class InboxSseState:
    last_payload: InboxPayload | None = None
    last_encoded: str = ""
    last_sent: str = ""
    idle_ticks: int = 0
    encode_calls: int = 0


@dataclass(slots=True)
class InboxSseFrame:
    text: str
    reused_encoding: bool = False


def next_inbox_sse_frame(
    state: InboxSseState,
    payload: InboxPayload,
    encode: EncodeInbox,
) -> InboxSseFrame | None:
    """Reuse the last encoding when the domain snapshot is unchanged."""
    reused = False
    if state.last_payload is not None and inbox_payloads_equal(payload, state.last_payload):
        encoded = state.last_encoded
        reused = True
    else:
        encoded = encode(payload)
        state.encode_calls += 1
        state.last_payload = payload
        state.last_encoded = encoded
    if encoded != state.last_sent:
        state.last_sent = encoded
        state.idle_ticks = 0
        return InboxSseFrame(f"event: inbox\ndata: {encoded}\n\n", reused)
    state.idle_ticks += 1
    if state.idle_ticks >= KEEP_ALIVE_IDLE_TICKS:
        state.idle_ticks = 0
        return InboxSseFrame(": keep-alive\n\n", reused)
    return None


def inbox_sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
