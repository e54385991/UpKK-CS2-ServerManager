"""Transport contracts shared by the versioned API.

Contracts deliberately live outside ORM models and services.  Domain modules
can depend on the small policy base classes here without importing routers.
"""

from .base import ApiRequest, ApiResponse

__all__ = ["ApiRequest", "ApiResponse"]
