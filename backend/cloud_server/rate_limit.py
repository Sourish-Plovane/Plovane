"""B05's per-user rate limiter (handbook §1.1 leaves the algorithm open).

A token bucket per key, in memory and thread-safe. The default is one token
per second with a burst of 60, which is generous for a person using the app
and cheap for the server to track. Multi-worker deployments need a shared
store (see the README), but one process is enough for development.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


# Input: capacity (float), refill_per_second (float), clock (Callable[[], float])
# Output: TokenBucket
# Processing: A bucket that starts full and refills continuously.
# Changed by: Nirmal
# Date: 2026-10-31
class TokenBucket:
    # Input: capacity (float), refill_per_second (float), clock (Callable[[], float])
    # Output: None
    # Processing: Validate the inputs and start the bucket full.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, capacity: float = 60.0, refill_per_second: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.clock = clock
        self.tokens = float(capacity)
        self.updated = clock()

    # Input: none
    # Output: bool
    # Processing: Refill for elapsed time, then spend one token if there is one.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_second)
        self.updated = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


# Input: capacity (float), refill_per_second (float), clock (Callable[[], float])
# Output: RateLimiter
# Processing: One token bucket per caller key, created on first use.
# Changed by: Nirmal
# Date: 2026-10-31
class RateLimiter:
    # Input: capacity (float), refill_per_second (float), clock (Callable[[], float])
    # Output: None
    # Processing: Hold the bucket parameters and the per-key bucket map.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, capacity: float = 60.0, refill_per_second: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.clock = clock
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    # Input: key (str)
    # Output: bool
    # Processing: Spend one token for the caller; False means rate-limited.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def allow(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.capacity, self.refill_per_second, self.clock)
                self._buckets[key] = bucket
            return bucket.take()
