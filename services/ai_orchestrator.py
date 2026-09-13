"""Bounded OpenAI Chat Completions tool-calling orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

from modules.database import async_session_maker
from modules.models import AISystemSettings, Server
from modules.utils import get_current_time
from services.agent_policy_service import (
    AgentCapabilityDenied,
    get_effective_agent_policy,
    require_agent_capabilities,
)
from services.ai.errors import AIPayloadTooLargeError
from services.ai.orchestrator.approvals import (
    _approval_is_expired,
    _approval_step_id,
    _approval_step_label,
    _build_plan_snapshots,
    _close_unexecuted_tools,
    reconcile_waiting_approval_runs,
)
from services.ai.orchestrator.lifecycle import (
    cleanup_expired_ai_runs,
    interrupt_active_ai_runs,
    interrupt_conversation_run,
    reconcile_stale_ai_server_lock,
)
from services.ai.orchestrator.progress import (
    _add_run_error_message,
    _AssistantDeltaEmitter,
    _emit,
    _finalize_progress_snapshot,
    _update_progress_snapshot,
)
from services.ai.orchestrator.provider import (
    _create_provider_response_with_retry,
    _fail_run,
    _retry_delay_seconds,
)
from services.ai.orchestrator.run_loop import process_ai_run
from services.ai.orchestrator.tools import (
    _execute_tool_run,
    _load_provider_messages,
    _resume_decided_tools,
    _validate_write_tool_batch,
)
from services.ai_access import audit_security_event, authorized_server
from services.ai_context import compact_if_needed, load_history, summary_message
from services.ai_prompt import build_system_prompt
from services.ai_provider import AIProviderError, create_chat_completion
from services.ai_security import (
    AIConfigurationError,
    AIProviderConfig,
    get_effective_provider,
    redact_sensitive_text,
    sanitize_tool_result,
)
from services.ai_tools import (
    TOOLS_BY_NAME,
    ToolContext,
    build_approval_summary,
    canonical_arguments,
    execute_tool,
    tool_definitions,
)
from services.ai_usage import estimate_message_tokens as _estimate_message_tokens
from services.ai_usage import estimate_response_tokens as _estimate_response_tokens
from services.ai_usage import provider_token_usage as _provider_token_usage
from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager

DEFAULT_MAX_PROVIDER_ROUNDS = 200
DEFAULT_MAX_TOOL_CALLS_PER_ROUND = 200
MAX_CONFIGURED_AI_LIMIT = 1000
MAX_REPEATED_CALLS = 3
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_approval")
AI_DELTA_EVENT_CHARS = 96
AI_WRITE_QUEUE_WAIT_SECONDS = 5 * 60
AI_WRITE_LOCK_TTL = 5 * 60
AI_RETRY_MAX_ATTEMPTS = 5
AI_RETRY_BASE_SECONDS = 15
AI_BACKGROUND_TASK_RETENTION_MINUTES = 10
AI_SERVER_LOCK_OPERATION_PREFIXES = ("ai:",)
AI_LEGACY_WRITE_TOOLS = (
    "apply_plugin_plan",
    "apply_github_plugin_install",
    "apply_workshop_map",
)
RUN_ERROR_TOOL_NAME = "__run_error__"
TERMINAL_RUN_STATUSES = ("completed", "failed", "interrupted", "expired", "cancelled")
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped", "interrupted"}
TERMINAL_STEP_STATUSES = {"completed", "failed", "skipped", "interrupted"}


_TOKEN_USAGE_EVENT = "token_usage"


def _token_usage_payload(response: Any) -> Any:
    """Stream provider usage as a token_usage event."""
    return _provider_token_usage(response)


# Settings cap applied in process_ai_run: max_tool_calls_per_round

_PATCHABLE = (
    AIConfigurationError,
    AISystemSettings,
    AgentCapabilityDenied,
    AIPayloadTooLargeError,
    AIProviderConfig,
    AIProviderError,
    TOOLS_BY_NAME,
    ToolContext,
    _AssistantDeltaEmitter,
    _add_run_error_message,
    _approval_is_expired,
    _approval_step_id,
    _approval_step_label,
    _build_plan_snapshots,
    _close_unexecuted_tools,
    _create_provider_response_with_retry,
    _emit,
    _estimate_message_tokens,
    _estimate_response_tokens,
    _execute_tool_run,
    _fail_run,
    _finalize_progress_snapshot,
    _load_provider_messages,
    _provider_token_usage,
    _resume_decided_tools,
    _retry_delay_seconds,
    _update_progress_snapshot,
    _validate_write_tool_batch,
    asyncio,
    async_session_maker,
    audit_security_event,
    authorized_server,
    build_approval_summary,
    build_system_prompt,
    canonical_arguments,
    cleanup_expired_ai_runs,
    compact_if_needed,
    create_chat_completion,
    execute_tool,
    get_current_time,
    get_effective_agent_policy,
    get_effective_provider,
    interrupt_active_ai_runs,
    interrupt_conversation_run,
    load_history,
    maintenance_lock_service,
    process_ai_run,
    reconcile_stale_ai_server_lock,
    Server,
    reconcile_waiting_approval_runs,
    redis_manager,
    redact_sensitive_text,
    require_agent_capabilities,
    sanitize_tool_result,
    summary_message,
    tool_definitions,
)
