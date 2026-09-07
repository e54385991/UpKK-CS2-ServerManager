"""Stable AI domain exceptions."""


class AIProviderError(RuntimeError):
    """Raised when an AI provider violates the configured transport contract."""

    def __init__(self, message: str, *, retryable: bool = False, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class AIPayloadTooLargeError(AIProviderError):
    """Raised when a provider rejects a request because its body is too large."""


def transient_provider_error(value: object) -> bool:
    """Recognize machine-readable provider failures without matching secret messages."""
    return isinstance(value, dict) and any(
        value.get(key)
        in {"rate_limit_error", "rate_limit_exceeded", "server_error", "overloaded_error"}
        for key in ("type", "code")
        if isinstance(value.get(key), str)
    )
