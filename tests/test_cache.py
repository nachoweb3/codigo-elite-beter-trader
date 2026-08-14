"""Tests para la caché TTL."""
import time

from app.services.cache import TTLCache


def test_set_and_get():
    cache = TTLCache(default_ttl=300)
    cache.set("clave", {"valor": 42})
    assert cache.get("clave") == {"valor": 42}


def test_missing_key_returns_none():
    cache = TTLCache()
    assert cache.get("no_existe") is None


def test_expiry():
    cache = TTLCache(default_ttl=0.1)
    cache.set("clave", "valor")
    assert cache.get("clave") == "valor"
    time.sleep(0.15)
    assert cache.get("clave") is None


def test_custom_ttl():
    cache = TTLCache(default_ttl=300)
    cache.set("clave", "valor", ttl=0.05)
    time.sleep(0.1)
    assert cache.get("clave") is None


def test_invalidate():
    cache = TTLCache()
    cache.set("clave", "valor")
    cache.invalidate("clave")
    assert cache.get("clave") is None


def test_clear():
    cache = TTLCache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert len(cache) == 0
