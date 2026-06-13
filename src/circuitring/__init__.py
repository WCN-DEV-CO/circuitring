"""circuitring — a tiny circuit breaker for Python. Zero deps. Sync + async."""
from .core import (
    CircuitBreaker,
    CircuitOpenError,
    State,
    circuit,
)

__version__ = "0.1.0"
__all__ = ["CircuitBreaker", "CircuitOpenError", "State", "circuit"]
