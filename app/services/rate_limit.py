"""Rate limiter de tipo token bucket para APIs externas (Helius).

Con una comunidad de hasta 100 personas analizando a la vez, las llamadas
a la API de Helius (y a otras APIs públicas) pueden dispararse de golpe y
superar los límites del plan (Helius free: ~50 req/min). Este limiter
espacia las peticiones globalmente para que el bucket nunca se vacíe del
todo y las llamadas fallidas por rate limit se reintenten con backoff.
"""
import asyncio
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket asíncrono: permite `capacity` peticiones a ráfaga,
    rellenando `rate` tokens por segundo. La espera es cooperativa
    (await) y justa (cola FIFO)."""

    def __init__(self, rate: float = 2.0, capacity: float = 8.0):
        self.rate = max(rate, 0.1)
        self.capacity = max(capacity, 1.0)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self._waiters = 0

    async def acquire(self) -> None:
        """Espera hasta que haya un token disponible y lo consume."""
        self._waiters += 1
        try:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    self._tokens = min(
                        self.capacity,
                        self._tokens + (now - self._updated) * self.rate,
                    )
                    self._updated = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    wait = (1 - self._tokens) / self.rate
                # Esperar fuera del lock para no bloquear a otros
                await asyncio.sleep(wait)
        finally:
            self._waiters -= 1

    @property
    def waiters(self) -> int:
        return self._waiters


class RateLimiter:
    """Limiter global para una API concreta (identificada por `name`).

    Uso:
        limiter = RateLimiter.get("helius", rate=3.0, capacity=10)
        async with limiter:
            await client.get(...)
    """

    _instances: dict = {}

    @classmethod
    def get(cls, name: str, rate: float = 3.0, capacity: float = 10.0) -> "RateLimiter":
        if name not in cls._instances:
            cls._instances[name] = cls(rate=rate, capacity=capacity)
        return cls._instances[name]

    def __init__(self, rate: float, capacity: float, name: str = "unknown"):
        self.bucket = TokenBucket(rate=rate, capacity=capacity)
        self.name = name
        self._hits = 0
        self._waits = 0

    async def __aenter__(self):
        waited = self.bucket.waiters > 0
        await self.bucket.acquire()
        self._hits += 1
        if waited:
            self._waits += 1
        return self

    async def __aexit__(self, *exc):
        return False

    def stats(self) -> dict:
        return {"name": self.name, "hits": self._hits, "waits": self._waits}

    def snapshot(self) -> dict:
        """Estado actual del limiter para exponerlo en /api/system/ratelimit."""
        return {
            "name": self.name,
            "hits": self._hits,
            "waits": self._waits,
            "rate_per_second": self.bucket.rate,
            "capacity": self.bucket.capacity,
            "tokens_available": round(self.bucket._tokens, 2),
            "waiters": self.bucket.waiters,
            "pressure": round((1 - self.bucket._tokens / self.bucket.capacity) * 100, 1),
        }


# Instancia global para Helius (compartida por todos los requests)
helius_limiter = RateLimiter.get("helius", rate=3.0, capacity=10)
helius_limiter.name = "helius"
