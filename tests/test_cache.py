from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import app.services.cache as cache_module
from app.services.cache import FileTTLCache, MemoryTTLCache


def test_memory_cache_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: now[0])
    cache = MemoryTTLCache()

    cache.set("weather", "sunny", ttl=10)
    assert cache.get("weather") == "sunny"

    now[0] = 111.0
    assert cache.get("weather") is None


def test_file_cache_survives_new_instance(tmp_path):
    first = FileTTLCache(tmp_path)
    first.set("attractions", "cached-value", ttl=60)

    second = FileTTLCache(tmp_path)

    assert second.get("attractions") == "cached-value"


def test_file_cache_concurrent_instances_use_separate_temp_files(
    tmp_path, monkeypatch
):
    barrier = Barrier(2)
    original_write_text = Path.write_text

    def write_text_together(path, data, *args, **kwargs):
        if path.suffix == ".tmp":
            barrier.wait(timeout=5)
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text_together)
    first = FileTTLCache(tmp_path)
    second = FileTTLCache(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writes = [
            executor.submit(cache.set, "same-key", value, 60)
            for cache, value in ((first, "first"), (second, "second"))
        ]
        for write in writes:
            write.result()

    assert first.get("same-key") in {"first", "second"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_file_cache_retries_transient_replace_error(tmp_path, monkeypatch):
    original_replace = Path.replace
    attempts = 0

    def replace_after_one_failure(path, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("destination temporarily locked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace_after_one_failure)
    cache = FileTTLCache(tmp_path)

    cache.set("same-key", "value", ttl=60)

    assert attempts == 2
    assert cache.get("same-key") == "value"
    assert list(tmp_path.glob("*.tmp")) == []
