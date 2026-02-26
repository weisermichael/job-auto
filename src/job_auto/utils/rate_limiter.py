"""Per-domain token bucket rate limiter with random jitter."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Refills at `rate` tokens/second; bursts up to `capacity`."""
    rate: float          # tokens per second
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume tokens. Returns True if successful."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def wait_time(self, tokens: float = 1.0) -> float:
        """Seconds to wait until `tokens` are available."""
        self._refill()
        deficit = tokens - self._tokens
        if deficit <= 0:
            return 0.0
        return deficit / self.rate


class RateLimiter:
    """
    Per-domain rate limiter.

    Default limits per domain:
    - linkedin.com  : 1 req / 3s
    - indeed.com    : 1 req / 2s
    - nodesk.co     : 1 req / 1s
    - (default)     : 1 req / 1s
    """

    _DOMAIN_RATES: dict[str, float] = {
        "linkedin.com": 1 / 3,
        "indeed.com": 1 / 2,
        "nodesk.co": 1.0,
    }

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket_for(self, domain: str) -> TokenBucket:
        if domain not in self._buckets:
            rate = self._DOMAIN_RATES.get(domain, 1.0)
            self._buckets[domain] = TokenBucket(rate=rate, capacity=max(1.0, rate * 5))
        return self._buckets[domain]

    def _extract_domain(self, url: str) -> str:
        for known in self._DOMAIN_RATES:
            if known in url:
                return known
        return "default"

    async def wait(self, url: str, jitter_max_ms: int = 800) -> None:
        """Async wait until the domain bucket allows a request, plus random jitter."""
        domain = self._extract_domain(url)
        bucket = self._bucket_for(domain)

        wait = bucket.wait_time()
        if wait > 0:
            await asyncio.sleep(wait)
        bucket.consume()

        jitter = random.uniform(0, jitter_max_ms / 1000)
        if jitter > 0:
            await asyncio.sleep(jitter)

    def wait_sync(self, url: str, jitter_max_ms: int = 800) -> None:
        """Synchronous version for non-async contexts."""
        domain = self._extract_domain(url)
        bucket = self._bucket_for(domain)

        wait = bucket.wait_time()
        if wait > 0:
            time.sleep(wait)
        bucket.consume()

        jitter = random.uniform(0, jitter_max_ms / 1000)
        if jitter > 0:
            time.sleep(jitter)


# Module-level singleton
rate_limiter = RateLimiter()
