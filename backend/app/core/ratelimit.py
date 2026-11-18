"""Fixed-window rate limiting in Redis. Fails closed: if Redis is down, requests are refused."""

from __future__ import annotations

import logging
import time
from functools import lru_cache

import redis

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable, TooManyRequests

log = logging.getLogger("rag.ratelimit")


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
    )


def hit(bucket: str, key: str, limit: int, window_s: int = 60) -> None:
    if limit <= 0:
        return
    window = int(time.time() // window_s)
    name = f"rl:{bucket}:{key}:{window}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(name)
        pipe.expire(name, window_s + 5)
        count = int(pipe.execute()[0])
    except redis.RedisError as exc:
        log.warning("rate limiter unavailable: %s", type(exc).__name__)
        raise ServiceUnavailable("Rate limiter unavailable, try again shortly") from exc
    if count > limit:
        raise TooManyRequests("Too many requests, slow down")
