"""Caché en memoria con expiración por TTL."""
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """Caché simple en memoria con expiración por tiempo.

    No es un cache distribuido: sirve para reducir llamadas repetidas
    a APIs externas (RPC, CoinGecko, Helius) dentro de un mismo proceso.
    """

    def __init__(self, default_ttl: float = 300.0):
        self.default_ttl = default_ttl
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = self.default_ttl if ttl is None else ttl
        self._store[key] = (time.monotonic() + ttl, value)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
