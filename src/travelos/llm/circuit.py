"""Compatibility import: the generic breaker now lives in ``travelos.core.circuit``."""

from __future__ import annotations

from travelos.core.circuit import CircuitBreaker

__all__ = ["CircuitBreaker"]
