"""
Redis-based global rate limiter using a sliding window counter.

All workers share a single Redis key.  ``INCR`` atomically increments the
counter, and the key expires after 1 second — Redis's expiration timer IS
the clock, so there is no drift between workers.  No Lua scripts, no token
filling, no background tasks.
"""

from __future__ import annotations

import os

import redis

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Get a shared Redis client from the ``REDIS_URL`` environment variable.

    The client is cached at module level so all calls within a worker
    process reuse the same connection pool.
    """
    global _client
    if _client is None:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _client = redis.from_url(redis_url)
    return _client


def try_acquire_request_permit(
    redis_client: redis.Redis | None = None,
    rate_limit: int | None = None,
    key: str = "lis:rate_limit",
) -> bool:
    """Attempt to acquire permission to make a LIS API request.

    Uses Redis ``INCR`` + ``EXPIRE(1)`` for global rate limiting across all
    workers.  The window resets every second via key expiration.

    Args:
        redis_client: Redis client instance.  If ``None``, uses the shared
            module-level client from ``get_redis_client()``.
        rate_limit: Max requests per second.  If ``None``, reads the
            ``LIS_RATE_LIMIT`` environment variable (default 100).
        key: Redis key for the counter.  Override for testing.

    Returns:
        ``True`` if allowed to proceed, ``False`` if rate limited.
    """
    if redis_client is None:
        redis_client = get_redis_client()

    if rate_limit is None:
        rate_limit = int(os.environ.get("LIS_RATE_LIMIT", "100"))

    # Pipeline batches INCR + TTL into a single round-trip
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = pipe.execute()

    if ttl == -1:
        redis_client.expire(key, 1)

    return count <= rate_limit
