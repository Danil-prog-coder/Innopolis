"""Process-local provider generation concurrency gate.

The gate is intentionally fail-fast: when all slots are busy, the caller gets a
typed busy terminal before any provider call, attempt id or ledger write. It is
not a distributed limit; multi-worker deployments need a shared limiter.

Acquire/release are synchronous and run on one event loop, so a cancelled
generator cannot lose its slot while awaiting a release lock.
"""


class ProviderConcurrencyLimiter:
    def __init__(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        self._limit = limit
        self._active = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        if self._active >= self._limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        if self._active <= 0:
            raise RuntimeError("provider slot released without acquire")
        self._active -= 1
