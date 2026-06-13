import asyncio
import pytest
from circuitring import CircuitBreaker, CircuitOpenError, State, circuit


class Clock:
    """Manual clock for deterministic time control."""
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def fail():
    raise ValueError("boom")

def ok():
    return "ok"


def test_starts_closed():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.state is State.CLOSED


def test_passes_through_when_closed():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.call(ok) == "ok"


def test_trips_after_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb.state is State.OPEN


def test_open_fails_fast():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown=10, clock=clk)
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail)
    # now OPEN — should fail fast WITHOUT calling fn
    calls = {"n": 0}
    def spy():
        calls["n"] += 1
    with pytest.raises(CircuitOpenError):
        cb.call(spy)
    assert calls["n"] == 0


def test_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=3)
    with pytest.raises(ValueError):
        cb.call(fail)
    with pytest.raises(ValueError):
        cb.call(fail)
    cb.call(ok)  # resets
    assert cb.failure_count == 0
    assert cb.state is State.CLOSED


def test_half_open_after_cooldown():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown=10, clock=clk)
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb.state is State.OPEN
    clk.advance(11)
    assert cb.state is State.HALF_OPEN


def test_half_open_success_closes():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown=10, success_threshold=1, clock=clk)
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail)
    clk.advance(11)
    assert cb.call(ok) == "ok"
    assert cb.state is State.CLOSED


def test_half_open_failure_reopens():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown=10, clock=clk)
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail)
    clk.advance(11)
    assert cb.state is State.HALF_OPEN
    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb.state is State.OPEN


def test_half_open_limits_trial_calls():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown=5, half_open_max_calls=1, clock=clk)
    with pytest.raises(ValueError):
        cb.call(fail)
    clk.advance(6)
    # first trial call allowed (we hold it open by not completing? simulate via before)
    cb._before_call()  # consumes the 1 allowed half-open slot
    with pytest.raises(CircuitOpenError):
        cb.call(ok)  # second trial blocked


def test_success_threshold_multiple():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown=5, success_threshold=2,
                        half_open_max_calls=5, clock=clk)
    with pytest.raises(ValueError):
        cb.call(fail)
    clk.advance(6)
    cb.call(ok)  # 1 of 2
    assert cb.state is State.HALF_OPEN
    cb.call(ok)  # 2 of 2 -> closes
    assert cb.state is State.CLOSED


def test_untracked_exceptions_dont_trip():
    cb = CircuitBreaker(failure_threshold=2, exceptions=ValueError)
    with pytest.raises(KeyError):
        cb.call(lambda: (_ for _ in ()).throw(KeyError("x")))
    assert cb.state is State.CLOSED
    assert cb.failure_count == 0


def test_decorator():
    cb = CircuitBreaker(failure_threshold=2)
    @cb
    def f(x):
        if x < 0:
            raise ValueError()
        return x * 2
    assert f(5) == 10
    with pytest.raises(ValueError):
        f(-1)
    with pytest.raises(ValueError):
        f(-1)
    assert cb.state is State.OPEN
    with pytest.raises(CircuitOpenError):
        f(5)


def test_context_manager():
    cb = CircuitBreaker(failure_threshold=1)
    with pytest.raises(ValueError):
        with cb:
            raise ValueError()
    assert cb.state is State.OPEN


def test_reset():
    cb = CircuitBreaker(failure_threshold=1)
    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb.state is State.OPEN
    cb.reset()
    assert cb.state is State.CLOSED


def test_retry_after_in_error():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown=30, clock=clk)
    with pytest.raises(ValueError):
        cb.call(fail)
    with pytest.raises(CircuitOpenError) as ei:
        cb.call(ok)
    assert 0 < ei.value.retry_after <= 30


def test_validation():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(half_open_max_calls=0)
    with pytest.raises(ValueError):
        CircuitBreaker(success_threshold=0)


def test_async_decorator():
    cb = CircuitBreaker(failure_threshold=2)
    @cb
    async def f(x):
        if x < 0:
            raise ValueError()
        return x + 1
    assert asyncio.run(f(1)) == 2
    async def trip():
        for _ in range(2):
            try:
                await f(-1)
            except ValueError:
                pass
    asyncio.run(trip())
    assert cb.state is State.OPEN


def test_factory():
    cb = circuit("payments", failure_threshold=3)
    assert cb.name == "payments"
    assert cb.failure_threshold == 3
