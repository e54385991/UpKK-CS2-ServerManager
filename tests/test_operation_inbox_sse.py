"""Inbox SSE frames reuse encodings only after a complete domain compare."""

from __future__ import annotations

from services.operations.inbox_equal import inbox_payloads_equal
from services.operations.inbox_sse import (
    KEEP_ALIVE_IDLE_TICKS,
    InboxSseState,
    next_inbox_sse_frame,
)
from services.operations.inbox_types import InboxItemData, InboxPayload


def _item(
    operation_id: str,
    *,
    status: str = "running",
    command: str = "plugin-market install 1",
    message: str | None = "Extracting",
    queue_position: int = 0,
    server_name: str = "alpha",
) -> InboxItemData:
    return InboxItemData(
        record={
            "operation_id": operation_id,
            "status": status,
            "command": command,
            "success": status == "completed",
            "completed_at": None if status in {"queued", "running"} else "2026-09-01T00:00:00Z",
        },
        server_name=server_name,
        queue_position=queue_position,
        latest_message=message,
    )


def _payload(
    items: list[InboxItemData] | None = None,
    *,
    failed: list[InboxItemData] | None = None,
    completed: list[InboxItemData] | None = None,
    import_jobs: list[object] | None = None,
    description_jobs: list[object] | None = None,
) -> InboxPayload:
    return InboxPayload(
        items=items or [],
        completed_items=completed or [],
        failed_items=failed or [],
        import_jobs=list(import_jobs or []),
        description_jobs=list(description_jobs or []),
    )


def test_payload_equality_covers_message_command_position_and_imports():
    left = _payload([_item("op-1")])
    right = _payload([_item("op-1")])
    assert inbox_payloads_equal(left, right) is True
    assert inbox_payloads_equal(left, _payload([_item("op-1", message="Done")])) is False
    assert inbox_payloads_equal(left, _payload([_item("op-1", command="other")])) is False
    assert inbox_payloads_equal(left, _payload([_item("op-1", queue_position=2)])) is False
    assert inbox_payloads_equal(left, _payload([_item("op-1")], import_jobs=["job"])) is False
    assert inbox_payloads_equal(left, _payload([_item("op-1")], description_jobs=["job"])) is False
    assert inbox_payloads_equal(left, _payload(failed=[_item("op-2", status="failed")])) is False


def test_sse_reuses_encoding_when_the_domain_snapshot_is_unchanged():
    state = InboxSseState()
    payload = _payload([_item("op-1")])
    encodes = {"count": 0}

    def encode(item: InboxPayload) -> str:
        encodes["count"] += 1
        return f"encoded-{item.items[0].record['operation_id']}-{encodes['count']}"

    first = next_inbox_sse_frame(state, payload, encode)
    second = next_inbox_sse_frame(state, _payload([_item("op-1")]), encode)
    assert first is not None and first.text.startswith("event: inbox\n")
    assert second is None
    assert encodes["count"] == 1
    assert state.encode_calls == 1


def test_sse_encodes_again_when_only_the_message_changes():
    state = InboxSseState()
    encodes: list[str] = []

    def encode(item: InboxPayload) -> str:
        text = item.items[0].latest_message or ""
        encodes.append(text)
        return text

    next_inbox_sse_frame(state, _payload([_item("op-1", message="one")]), encode)
    frame = next_inbox_sse_frame(state, _payload([_item("op-1", message="two")]), encode)
    assert frame is not None
    assert frame.reused_encoding is False
    assert encodes == ["one", "two"]
    assert "two" in frame.text


def test_sse_still_compares_encoded_json_after_a_payload_change():
    state = InboxSseState()

    def encode(_payload: InboxPayload) -> str:
        return "same-bytes"

    first = next_inbox_sse_frame(state, _payload([_item("op-1", message="one")]), encode)
    second = next_inbox_sse_frame(state, _payload([_item("op-1", message="two")]), encode)
    assert first is not None
    assert second is None
    assert state.encode_calls == 2


def test_sse_keep_alive_after_unchanged_idle_ticks():
    state = InboxSseState()

    def encode(_payload: InboxPayload) -> str:
        return "same"

    first = next_inbox_sse_frame(state, _payload(), encode)
    assert first is not None
    last = None
    for _ in range(KEEP_ALIVE_IDLE_TICKS):
        last = next_inbox_sse_frame(state, _payload(), encode)
    assert last is not None
    assert last.text == ": keep-alive\n\n"
    assert state.encode_calls == 1
