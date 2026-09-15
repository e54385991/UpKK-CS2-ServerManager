"""Complete inbox payload equality for one SSE connection. Not a shared cache."""

from __future__ import annotations

from collections.abc import Sequence

from services.operations.inbox_types import InboxItemData, InboxPayload


def inbox_items_equal(left: InboxItemData, right: InboxItemData) -> bool:
    return (
        left.server_name == right.server_name
        and left.queue_position == right.queue_position
        and left.latest_message == right.latest_message
        and left.record == right.record
    )


def inbox_item_lists_equal(left: Sequence[InboxItemData], right: Sequence[InboxItemData]) -> bool:
    if len(left) != len(right):
        return False
    return all(inbox_items_equal(first, second) for first, second in zip(left, right, strict=True))


def inbox_payloads_equal(left: InboxPayload, right: InboxPayload) -> bool:
    """Compare messages, commands, positions, results, imports, and retention."""
    return (
        left.completed_retention_days == right.completed_retention_days
        and left.failed_retention_days == right.failed_retention_days
        and inbox_item_lists_equal(left.items, right.items)
        and inbox_item_lists_equal(left.completed_items, right.completed_items)
        and inbox_item_lists_equal(left.failed_items, right.failed_items)
        and left.import_jobs == right.import_jobs
    )
