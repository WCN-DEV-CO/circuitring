"""
circuitring
===========
A tiny, dependency-free **circuit breaker** for Python. Sync + async.

A circuit breaker protects a caller from a failing dependency. Instead of
hammering a service that's already down (piling on load and blocking your own
threads on doomed calls), the breaker "trips" after N consecutive failures and
fails fast for a cooldown window. After the cooldown it lets a few trial calls
through (HALF_OPEN); if they succeed it closes again, if they fail it re-opens.

Three states:
    CLOSED     — normal. Calls pass through. Failures are counted.
    OPEN       — tripped. Calls fail fast with CircuitOpenError until cooldown ends.
    HALF_OPEN  — trial. A limited number of calls are allowed through to test
                 recovery. Success closes the circuit; failure re-opens it.

Pure standard library. No dependencies.
"""
from __future__ import annotations

import asyncio
import functools
import threading
import time
from enum import Enum
from typing import Callable, Iterable, Type, TypeVar

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"circuit '{name}' is OPEN; retry in {retry_after:.2f}s"
        )


class CircuitBreaker:
    """A thread-safe circuit breaker.

    Args:
        name: label used in errors / introspection.
        failure_threshold: consecutive failures in CLOSED that trip the circuit.
        cooldown: seconds to stay OPEN before allowing trial calls.
        half_open_max_calls: trial calls allowed in HALF_OPEN at once.
        success_threshold: consecutive successes in HALF_OPEN required to close.
        exceptions: which exception types count as failures (others pass through
            untouched and do NOT trip the breaker).
        clock: injectable time source (for tests).

    Use it three ways:
        * as a decorator:           ``@breaker``
        * as a context manager:     ``with breaker: ...``
        * by calling:               ``breaker.call(fn, *args, **kwargs)``
    """

    def __init__(
        self,
        name: str = "circuit",
        *,
        failure_threshold: int = 5,
        cooldown: float = 30.0,
        half_open_max_calls: int = 1,
        success_threshold: int = 1,
        exceptions: Type[BaseException] | Iterable[Type[BaseException]] = Exception,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        if success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")

        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self._exc = (exceptions,) if isinstance(exceptions, type) else tuple(exceptions)
        self._clock = clock

        self._lock = threading.RLock()
        self._state = State.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0
        self._half_open_calls = 0

    # ---- introspection ----
    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_to_half_open()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def _retry_after(self) -> float:
        return max(0.0, self.cooldown - (self._clock() - self._opened_at))

    # ---- state transitions (call with lock held) ----
    def _maybe_to_half_open(self) -> None:
        if self._state is State.OPEN and self._retry_after() <= 0.0:
            self._state = State.HALF_OPEN
            self._half_open_calls = 0
            self._successes = 0

    def _on_success(self) -> None:
        with self._lock:
            if self._state is State.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._reset_closed()
            else:
                self._failures = 0

    def _on_failure(self) -> None:
        with self._lock:
            if self._state is State.HALF_OPEN:
                self._trip()
            else:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._trip()

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0

    def _reset_closed(self) -> None:
        self._state = State.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0

    def _before_call(self) -> None:
        """Decide whether a call is allowed. Raises CircuitOpenError if not."""
        with self._lock:
            self._maybe_to_half_open()
            if self._state is State.OPEN:
                raise CircuitOpenError(self.name, self._retry_after())
            if self._state is State.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(self.name, self._retry_after())
                self._half_open_calls += 1

    def reset(self) -> None:
        """Force the circuit back to CLOSED."""
        with self._lock:
            self._reset_closed()

    # ---- the three usage styles ----
    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        self._before_call()
        try:
            result = fn(*args, **kwargs)
        except self._exc as e:  # type: ignore[misc]
            self._on_failure()
            raise
        except BaseException:
            # not a tracked failure type — don't affect the breaker
            raise
        else:
            self._on_success()
            return result

    async def call_async(self, fn: Callable[..., "T"], *args, **kwargs) -> T:
        self._before_call()
        try:
            result = await fn(*args, **kwargs)
        except self._exc as e:  # type: ignore[misc]
            self._on_failure()
            raise
        except BaseException:
            raise
        else:
            self._on_success()
            return result

    def __enter__(self):
        self._before_call()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and issubclass(exc_type, self._exc):
            self._on_failure()
        elif exc_type is None:
            self._on_success()
        return False  # never suppress

    def __call__(self, fn: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                return await self.call_async(fn, *args, **kwargs)

            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return self.call(fn, *args, **kwargs)

        return wrapper


def circuit(name: str = "circuit", **kwargs) -> CircuitBreaker:
    """Convenience factory. ``@circuit("payments", failure_threshold=3)``"""
    return CircuitBreaker(name, **kwargs)
