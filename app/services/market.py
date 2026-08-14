"""Servicio de datos de mercado con caché y fuentes redundantes."""
import httpx
from app.services.cache import TTLCache
from app.config import get_settings

settings = get_settings()

_sol_price_cache = TTLCache(default_ttl=settings.CACHE_TTL)


async def get_sol_price_usd() -> float:
    """Precio de SOL en USD con caché (CoinGecko + Binance como fallback)."""
    cached = _sol_price_cache.get("sol_price_usd")
    if cached is not None:
        return cached

    price = 0.0

    # Fuente 1: CoinGecko
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            price = float(resp.json().get("solana", {}).get("usd", 0) or 0)
    except Exception:
        price = 0.0

    # Fuente 2: Binance (fallback)
    if not price:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": "SOLUSDT"},
                )
                resp.raise_for_status()
                price = float(resp.json().get("price", 0) or 0)
        except Exception:
            price = 0.0

    if price > 0:
        _sol_price_cache.set("sol_price_usd", price)

    return price
