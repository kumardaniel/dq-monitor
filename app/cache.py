"""Redis-backed read cache and rate limiter.

Cache pattern: cache-aside.
  read:  try Redis -> on miss, compute from the DB and store with a TTL
  write: change the DB, commit, then DELETE the affected keys (invalidation)

Why delete instead of updating the cached value? Deleting is simple and can't write a
wrong value; the next read rebuilds it. The TTL is a safety net: if an invalidation is
ever missed (or races with a concurrent read that re-caches an old value), the stale
entry still disappears within cache_ttl_seconds.

Fail-open: if Redis is down or not configured, every call degrades to "cache miss" /
"not rate limited" and the API keeps working off the database.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import redis

from app.config import get_settings
from app.observability import CACHE_REQUESTS, log_extra

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
_client_initialised = False


def get_client() -> redis.Redis | None:
    global _client, _client_initialised
    if not _client_initialised:
        url = get_settings().redis_url
        if url:
            _client = redis.Redis.from_url(
                url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5
            )
        _client_initialised = True
    return _client


def set_client(client: redis.Redis | None) -> None:
    """Swap the client (tests use fakeredis)."""
    global _client, _client_initialised
    _client, _client_initialised = client, True


# ---- Keys ------------------------------------------------------------------
def source_status_key(source_id: int) -> str:
    return f"dq:cache:source_status:{source_id}"


SUMMARY_KEY = "dq:cache:summary"


# ---- Read-through ----------------------------------------------------------
def get_or_set(key: str, compute: Callable[[], Any]) -> tuple[Any, bool]:
    """Return (value, hit). `compute` must return something JSON-serialisable."""
    client = get_client()
    if client is not None:
        try:
            cached = client.get(key)
            if cached is not None:
                CACHE_REQUESTS.labels("hit").inc()
                return json.loads(cached), True
        except redis.RedisError as exc:
            logger.warning("cache read failed", extra=log_extra(key=key, error=str(exc)))
    CACHE_REQUESTS.labels("miss").inc()
    value = compute()
    if client is not None:
        try:
            client.set(key, json.dumps(value, default=str), ex=get_settings().cache_ttl_seconds)
        except redis.RedisError as exc:
            logger.warning("cache write failed", extra=log_extra(key=key, error=str(exc)))
    return value, False


def invalidate(*keys: str) -> None:
    client = get_client()
    if client is None or not keys:
        return
    try:
        client.delete(*keys)
    except redis.RedisError as exc:
        logger.warning("cache invalidation failed", extra=log_extra(keys=keys, error=str(exc)))


def invalidate_source(source_id: int | None) -> None:
    """Anything that changes a source's runs, rules or alerts calls this."""
    keys = [SUMMARY_KEY]
    if source_id is not None:
        keys.append(source_status_key(source_id))
    invalidate(*keys)


# ---- Rate limiting ---------------------------------------------------------
def hit_rate_limit(bucket: str, limit: int, window_seconds: int = 60) -> int | None:
    """Fixed-window counter. Returns seconds until reset if over the limit, else None.

    INCR is atomic in Redis, so concurrent requests across many API replicas are
    counted exactly. The first INCR in a window also sets the key's expiry.
    """
    client = get_client()
    if client is None or limit <= 0:
        return None
    window = int(time.time() // window_seconds)
    key = f"dq:ratelimit:{bucket}:{window}"
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
    except redis.RedisError as exc:
        logger.warning("rate limiter unavailable", extra=log_extra(error=str(exc)))
        return None
    if count > limit:
        return window_seconds - int(time.time() % window_seconds)
    return None
