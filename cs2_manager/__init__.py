"""Stable package boundary for the CS2 Server Manager application.

The historical top-level :mod:`api`, :mod:`modules`, and :mod:`services`
packages remain available while features are migrated behind this namespace.
"""

from cs2_manager.core import AppContainer, ErrorResponse, Principal

__all__ = ["AppContainer", "ErrorResponse", "Principal"]
