"""上下文自动压缩：按上下文窗口折叠旧历史，并保持上游提示词缓存前缀稳定。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import AIMessage
from services import ai_context
from services.ai.errors import AIProviderError
from services.ai_security import AIProviderConfig


class _Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    """Returns the queued rows for the single history query the loader makes."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, _query):
        return _Rows(self.rows)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


def _conversation(**values):
    defaults = dict(id="c", summary=None, summary_message_id=None, summary_tokens=0)
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _config(window=8_192, output=512):
    return AIProviderConfig(
        base_url="https://provider.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=30,
        allowlist=(),
        source="system",
        context_window_tokens=window,
        max_completion_tokens=output,
        reasoning_effort="high",
    )


def _message(row_id: int, role: str = "user", content: str = "hello", **values) -> AIMessage:
    return AIMessage(
        id=row_id,
        conversation_id="c",
        role=role,
        content=content,
        visible=True,
        **values,
    )


def test_input_budget_holds_back_the_output_reservation():
    assert ai_context.input_token_budget(_config(8_192, 512)) == 7_680
    # A provider stub without either field still yields a usable budget.
    assert ai_context.input_token_budget(SimpleNamespace()) == 1


def test_summary_message_is_omitted_when_there_is_no_summary():
    assert ai_context.summary_message(None) is None
    assert ai_context.summary_message("   ") is None
    assert ai_context.SUMMARY_PREFIX in str(ai_context.summary_message("facts")["content"])


@pytest.mark.asyncio
async def test_history_reads_newest_rows_and_drops_orphan_tool_results():
    # The query is newest-first; the loader reverses it into send order.
    rows = [
        _message(3, "tool", "result", tool_call_id="call", tool_name="read"),
        _message(2, "assistant", "", tool_calls=[{"id": "call"}]),
        _message(1, "user", "question"),
    ]
    history, truncated = await ai_context.load_history(_Session(rows), _conversation())
    assert [item[0] for item in history] == [1, 2, 3]
    assert history[2][1]["tool_call_id"] == "call"
    assert truncated is False

    orphan, truncated = await ai_context.load_history(_Session(rows[:1]), _conversation())
    assert orphan == []
    assert truncated is True


@pytest.mark.asyncio
async def test_history_over_the_row_limit_keeps_the_newest_and_reports_truncation():
    rows = [_message(index) for index in range(ai_context.HISTORY_ROW_LIMIT + 5, 0, -1)]
    history, truncated = await ai_context.load_history(_Session(rows), _conversation())
    assert truncated is True
    assert len(history) == ai_context.HISTORY_ROW_LIMIT
    # The live request is at the end and must never be the part that falls off.
    assert history[-1][0] == ai_context.HISTORY_ROW_LIMIT + 5


@pytest.mark.asyncio
async def test_history_within_budget_is_sent_untouched(monkeypatch):
    summarize = AsyncMock()
    monkeypatch.setattr(ai_context, "create_chat_completion", summarize)
    conversation = _conversation()
    db = _Session()
    history = [(1, {"role": "user", "content": "hi"})]
    result = await ai_context.compact_if_needed(
        db, conversation, _config(), [{"role": "system", "content": "prompt"}], history
    )
    assert result.compacted is False
    assert result.messages == [{"role": "system", "content": "prompt"}, history[0][1]]
    summarize.assert_not_awaited()
    assert db.commits == 0


@pytest.mark.asyncio
async def test_overflow_folds_the_oldest_groups_into_a_persisted_summary(monkeypatch):
    create = AsyncMock(return_value={"content": "operator asked about server 4"})
    monkeypatch.setattr(ai_context, "create_chat_completion", create)
    conversation = _conversation(summary="older facts")
    db = _Session()
    history = [(index, {"role": "user", "content": "x" * 4_000}) for index in range(1, 21)]

    result = await ai_context.compact_if_needed(
        db, conversation, _config(8_192, 512), [{"role": "system", "content": "prompt"}], history
    )

    assert result.compacted is True
    assert result.dropped_messages > 0
    assert conversation.summary == "operator asked about server 4"
    assert conversation.summary_message_id == result.dropped_messages
    assert conversation.summary_tokens > 0
    assert db.commits == 1
    # System prompt, then the summary, then the retained tail.
    assert result.messages[0]["content"] == "prompt"
    assert "operator asked about server 4" in result.messages[1]["content"]
    assert len(result.messages) == 2 + (len(history) - result.dropped_messages)
    # The last message is always kept: it is the request being answered.
    assert result.messages[-1] is history[-1][1]
    # The previous summary is carried into the new one instead of being lost.
    assert "older facts" in create.await_args.args[1][1]["content"]
    assert create.await_args.kwargs["stream"] is False


@pytest.mark.asyncio
async def test_tool_results_stay_with_the_call_that_produced_them(monkeypatch):
    monkeypatch.setattr(
        ai_context, "create_chat_completion", AsyncMock(return_value={"content": "summary"})
    )
    history: list[tuple[int, dict]] = []
    for index in range(0, 30, 3):
        history.append((index + 1, {"role": "user", "content": "y" * 2_000}))
        history.append(
            (index + 2, {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{index}"}]})
        )
        history.append(
            (index + 3, {"role": "tool", "content": "z" * 2_000, "tool_call_id": f"c{index}"})
        )
    result = await ai_context.compact_if_needed(
        _Session(), _conversation(), _config(8_192, 512), [], history
    )

    assert result.compacted is True
    sent = [message for message in result.messages if message.get("role") in {"assistant", "tool"}]
    for position, message in enumerate(sent):
        if message["role"] == "tool":
            assert sent[position - 1].get("tool_calls"), "orphaned tool result was sent"


@pytest.mark.asyncio
async def test_a_failed_summary_leaves_history_and_the_conversation_untouched(monkeypatch):
    monkeypatch.setattr(
        ai_context,
        "create_chat_completion",
        AsyncMock(side_effect=AIProviderError("provider down")),
    )
    conversation = _conversation()
    db = _Session()
    history = [(index, {"role": "user", "content": "x" * 4_000}) for index in range(1, 21)]

    result = await ai_context.compact_if_needed(db, conversation, _config(8_192, 512), [], history)

    # The byte-level trim in ai_provider remains the last-resort guard.
    assert result.compacted is False
    assert conversation.summary is None
    assert db.commits == 0
    assert len(result.messages) == len(history)


@pytest.mark.asyncio
async def test_a_single_oversized_group_is_left_to_the_provider_side_trim(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(ai_context, "create_chat_completion", create)
    conversation = _conversation()
    history = [(1, {"role": "user", "content": "x" * 200_000})]

    result = await ai_context.compact_if_needed(
        _Session(), conversation, _config(8_192, 512), [], history
    )

    assert result.compacted is False
    create.assert_not_awaited()
    assert conversation.summary is None


@pytest.mark.asyncio
async def test_truncated_history_records_that_older_messages_are_unavailable(monkeypatch):
    create = AsyncMock(return_value={"content": "summary"})
    monkeypatch.setattr(ai_context, "create_chat_completion", create)
    history = [(index, {"role": "user", "content": "x" * 4_000}) for index in range(1, 21)]

    await ai_context.compact_if_needed(
        _Session(), _conversation(), _config(8_192, 512), [], history, truncated=True
    )

    assert "older than the retained history window" in create.await_args.args[1][1]["content"]


@pytest.mark.asyncio
async def test_summary_input_is_bounded_and_an_empty_transcript_keeps_the_summary(monkeypatch):
    create = AsyncMock(return_value={"content": "x" * (ai_context.SUMMARY_MAX_CHARS + 500)})
    monkeypatch.setattr(ai_context, "create_chat_completion", create)
    assert await ai_context._summarize(_config(), "kept", []) == "kept"

    # A non-string body, a tool-call body and enough bulk to hit the input cap.
    entries: list[tuple[int, dict]] = [
        (0, {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}], "name": "read"})
    ]
    entries += [(index, {"role": "user", "content": "y" * 4_000}) for index in range(1, 41)]
    summary = await ai_context._summarize(_config(), None, entries)
    assert summary is not None
    assert len(summary) == ai_context.SUMMARY_MAX_CHARS
    transcript = create.await_args.args[1][1]["content"]
    assert len(transcript) < ai_context.SUMMARY_INPUT_CHARS + 200
    assert "earlier lines omitted" in transcript

    create.return_value = {"content": "   "}
    assert await ai_context._summarize(_config(), None, entries) is None
