# circuitring

A tiny, dependency-free **circuit breaker** for Python. Sync & async.

```python
from circuitring import CircuitBreaker

db = CircuitBreaker("db", failure_threshold=5, cooldown=30)

@db
def query(sql):
    return run(sql)
```

After 5 consecutive failures the breaker trips: further calls **fail fast**
instead of piling load onto a service that's already down. After a cooldown it
lets a trial call through to test recovery, then closes again if it succeeds.

No dependencies. Pure standard library.

## Why a circuit breaker?

When a dependency goes down, naive code keeps calling it — every call blocks,
times out, and stacks up, which can drag your *own* service down with it. A
circuit breaker detects the failure, stops calling for a cooldown window
(failing fast), and probes for recovery before resuming. It's the classic
pattern from Nygard's *Release It!* and Hystrix.

## States

```
CLOSED      normal — calls pass through, failures are counted
   │  failure_threshold consecutive failures
   ▼
OPEN        tripped — calls fail fast with CircuitOpenError
   │  cooldown elapses
   ▼
HALF_OPEN   trial — limited calls allowed; success → CLOSED, failure → OPEN
```

## Install

```bash
pip install circuitring
```

Or copy the single file `src/circuitring/core.py` into your project.

## Usage

Three interchangeable styles:

### As a decorator
```python
from circuitring import CircuitBreaker

api = CircuitBreaker("api", failure_threshold=3, cooldown=15)

@api
def fetch(url):
    return requests.get(url)
```

### As a context manager
```python
with api:
    do_risky_thing()
```

### By calling
```python
api.call(do_risky_thing, arg1, arg2)
```

### Async
```python
@api
async def fetch(url):
    ...
```

## Failing fast

When the circuit is OPEN, calls raise `CircuitOpenError` immediately — the
wrapped function is never even invoked:

```python
from circuitring import CircuitOpenError

try:
    fetch("https://flaky.example")
except CircuitOpenError as e:
    print(f"skipping; retry in {e.retry_after:.1f}s")
```

## Choosing which errors count

By default any `Exception` counts as a failure. Narrow it so expected,
non-infrastructure errors don't trip the breaker:

```python
CircuitBreaker("api", exceptions=(ConnectionError, TimeoutError))
```

Exceptions outside that set propagate normally and leave the breaker untouched.

## Tuning

| Param | Meaning | Default |
|-------|---------|---------|
| `failure_threshold` | consecutive failures that trip the circuit | 5 |
| `cooldown` | seconds to stay OPEN before a trial | 30.0 |
| `half_open_max_calls` | trial calls allowed at once in HALF_OPEN | 1 |
| `success_threshold` | consecutive successes in HALF_OPEN to close | 1 |
| `exceptions` | which exception types count as failures | `Exception` |

## Introspection

```python
api.state          # State.CLOSED / OPEN / HALF_OPEN
api.failure_count  # current consecutive failures
api.reset()        # force back to CLOSED
```

## Pairs well with

- **[retry-jitter](https://github.com/WCN-DEV-CO/retry-jitter)** — retries with backoff + jitter
- **[tierbroker](https://github.com/WCN-DEV-CO/tierbroker)** — fair scheduling + failover across providers

## Running the tests

```bash
pip install -e ".[test]"
pytest
```

## License

MIT © WCN Development Co

---

Built and maintained by **[WCN Development Co](https://github.com/WCN-DEV-CO)**.
Building resilient systems at scale, or want to partner / integrate / work with
us? Open an issue — we'd love to hear from you.
