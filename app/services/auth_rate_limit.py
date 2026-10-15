import os
from hashlib import sha256
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError


LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if count > tonumber(ARGV[2]) then
    return redis.call('TTL', KEYS[1])
end
return 0
"""
MAX_LOCAL_BUCKETS = 10_000


class AuthRateLimiter:
    def __init__(self, redis_client: Redis | None = None) -> None:
        self.redis = redis_client
        self._buckets: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def enforce(self, scope: str, identity: str, limit: int, period: int) -> None:
        digest = sha256(identity.encode("utf-8")).hexdigest()
        key = f"travel:auth:{scope}:{digest}"

        if self.redis is not None:
            try:
                retry_after = int(self.redis.eval(LIMIT_SCRIPT, 1, key, period, limit))
            except RedisError as exc:
                raise HTTPException(
                    status_code=503, detail="Authentication temporarily unavailable"
                ) from exc
        else:
            with self._lock:
                now = monotonic()
                count, expires_at = self._buckets.get(key, (0, now + period))
                if expires_at <= now:
                    count, expires_at = 0, now + period
                if key not in self._buckets and len(self._buckets) >= MAX_LOCAL_BUCKETS:
                    self._buckets = {
                        name: value for name, value in self._buckets.items()
                        if value[1] > now
                    }
                    if len(self._buckets) >= MAX_LOCAL_BUCKETS:
                        raise HTTPException(
                            status_code=503, detail="Authentication temporarily unavailable"
                        )
                count += 1
                self._buckets[key] = (count, expires_at)
                retry_after = ceil(expires_at - now) if count > limit else 0

        if retry_after != 0:
            retry_after = max(1, retry_after)
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts",
                headers={"Retry-After": str(retry_after)},
            )


def create_auth_rate_limiter() -> AuthRateLimiter:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return AuthRateLimiter()
    return AuthRateLimiter(Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
    ))


def get_auth_rate_limiter(request: Request) -> AuthRateLimiter:
    return request.app.state.auth_rate_limiter
