import json
import os
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic, sleep, time
from typing import Any
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError


class MemoryTTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}
        self._lock = Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._values.get(key)

            if item is None:
                return None

            expires_at, value = item
            if monotonic() >= expires_at:
                self._values.pop(key, None)
                return None

            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._values[key] = (monotonic() + ttl, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)


class FileTTLCache:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _path(self, key: str) -> Path:
        digest = sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> str | None:
        path = self._path(key)

        with self._lock:
            if not path.exists():
                return None

            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                return None

            if time() >= float(item["expires_at"]):
                path.unlink(missing_ok=True)
                return None

            return str(item["value"])

    def set(self, key: str, value: str, ttl: int) -> None:
        path = self._path(key)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        payload = json.dumps(
            {
                "expires_at": time() + ttl,
                "value": value,
            },
            ensure_ascii=False,
        )

        with self._lock:
            try:
                temporary.write_text(payload, encoding="utf-8")
                for attempt in range(5):
                    try:
                        temporary.replace(path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        sleep(0.01 * 2**attempt)
            finally:
                temporary.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        with self._lock:
            self._path(key).unlink(missing_ok=True)


class CacheService:
    def __init__(self) -> None:
        self.memory = MemoryTTLCache()
        self.file = FileTTLCache(
            os.getenv("CACHE_PATH", "data/cache")
        )
        self.redis: Redis | None = None
        redis_url = os.getenv("REDIS_URL")

        if redis_url:
            self.redis = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
            )

    @property
    def backend_name(self) -> str:
        return "redis" if self.redis is not None else "file"

    def _disable_redis(self) -> None:
        self.redis = None

    def get_json(self, key: str) -> Any | None:
        value = None

        if self.redis is not None:
            try:
                value = self.redis.get(key)
            except RedisError:
                self._disable_redis()

        if value is None:
            value = self.memory.get(key)
        if value is None:
            value = self.file.get(key)

        return json.loads(value) if value is not None else None

    def set_json(self, key: str, value: Any, ttl: int = 3600) -> None:
        serialized = json.dumps(value, ensure_ascii=False)
        stored_in_redis = False

        if self.redis is not None:
            try:
                self.redis.setex(key, ttl, serialized)
                stored_in_redis = True
            except RedisError:
                self._disable_redis()

        if not stored_in_redis:
            self.memory.set(key, serialized, ttl)
            self.file.set(key, serialized, ttl)

    def delete(self, key: str) -> None:
        if self.redis is not None:
            try:
                self.redis.delete(key)
            except RedisError:
                self._disable_redis()

        self.memory.delete(key)
        self.file.delete(key)


cache = CacheService()


def task_key(trip_id: int) -> str:
    return f"travel:task:{trip_id}"


def set_task_state(
    trip_id: int,
    status: str,
    *,
    node: str | None = None,
    message: str | None = None,
    error: str | None = None,
    plan: dict | None = None,
) -> None:
    cache.set_json(
        task_key(trip_id),
        {
            "trip_id": trip_id,
            "status": status,
            "node": node,
            "message": message,
            "error": error,
            "plan": plan,
        },
        ttl=24 * 60 * 60,
    )


def get_task_state(trip_id: int) -> dict | None:
    return cache.get_json(task_key(trip_id))
