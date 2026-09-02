"""Stable AI domain exceptions."""


class AIProviderError(RuntimeError):
    """Raised when an AI provider violates the configured transport contract."""


class AIPayloadTooLargeError(AIProviderError):
    """Raised when a provider rejects a request because its body is too large."""
