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
