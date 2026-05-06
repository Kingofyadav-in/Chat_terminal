"""Token bucket rate limiter for per-connection/per-key throttling."""

import time


class RateLimiter:
    """Token bucket rate limiter.

    Each key gets its own bucket that refills at ``rate`` tokens per ``per``
    seconds.  A single call to ``allow()`` consumes one token; when the bucket
    is empty the request is denied.  All operations are O(1) and safe for use
    in a single-threaded asyncio event loop.
    """

    def __init__(self, rate: int = 100, per: float = 60.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be a positive integer")
        if per <= 0.0:
            raise ValueError("per must be a positive float")
        self._rate = rate          # max tokens / bucket capacity
        self._per = per            # refill window in seconds
        # key → [tokens_remaining: float, last_check: float]
        self._buckets: dict[str, list] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow(self, key: str) -> bool:
        """Return True and consume a token if the request is within limit.

        Returns False (without consuming a token) when the bucket is empty.
        Tokens are refilled continuously based on elapsed time since the last
        check.
        """
        now = time.monotonic()
        if key not in self._buckets:
            # First request: full bucket, consume one token immediately.
            self._buckets[key] = [float(self._rate) - 1.0, now]
            return True

        tokens, last = self._buckets[key]
        elapsed = now - last
        # Refill tokens proportional to elapsed time.
        tokens = min(float(self._rate), tokens + elapsed * (self._rate / self._per))
        self._buckets[key][1] = now  # update timestamp regardless

        if tokens < 1.0:
            self._buckets[key][0] = tokens
            return False

        self._buckets[key][0] = tokens - 1.0
        return True

    def reset(self, key: str) -> None:
        """Remove the bucket for *key* (call on client disconnect)."""
        self._buckets.pop(key, None)
