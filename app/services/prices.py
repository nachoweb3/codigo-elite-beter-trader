"""Servicio de precios de tokens en tiempo real (sin API key).

Fuentes:
1. DexScreener (gratis, sin key) - precios de pares on-chain
2. GeckoTerminal (gratis, sin key) - fallback
3. Tokens conocidos (SOL/USDC/USDT) - precio fijo por USD peg

Todo con caché TTL corto (60s) para no abusar de las APIs.
"""
import httpx
from typing import Dict, Optional
from app.services.cache import TTLCache

# Tokens conocidos: precio en USD (peg o hardcode)
KNOWN_TOKEN_PRICES: Dict[str, float] = {
    "So11111111111111111111111111111111111111112": None,  # SOL: dinámico
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0,  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 1.0,  # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": None,  # mSOL: dinámico
}

SOL_MINT = "So11111111111111111111111111111111111111112"

_price_cache = TTLCache(default_ttl=60)  # 60 segundos

from app.services.market import get_sol_price_usd


async def get_token_price_usd(mint: str) -> Optional[float]:
    """Precio actual de un token en USD (con caché de 60s)."""
    if not mint:
        return None

    # Tokens conocidos
    if mint in KNOWN_TOKEN_PRICES:
        if mint == SOL_MINT:
            price = await get_sol_price_usd()
            return price if price > 0 else None
        return KNOWN_TOKEN_PRICES[mint]

    cached = _price_cache.get(f"price_usd:{mint}")
    if cached is not None:
        return cached

    price = None

    # Fuente 1: DexScreener
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            resp.raise_for_status()
            data = resp.json()
            pairs = data.get("pairs") or []
            # Priorizar pares con más volumen en 24h
            if pairs:
                pairs.sort(key=lambda p: (p.get("volume", {}) or {}).get("h24") or 0, reverse=True)
                for pair in pairs:
                    price_str = pair.get("priceUsd")
                    if price_str:
                        try:
                            price = float(price_str)
                            break
                        except (TypeError, ValueError):
                            continue
    except Exception:
        price = None

    # Fuente 2: GeckoTerminal
    if not price:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}"
                )
                resp.raise_for_status()
                data = resp.json()
                attr = (data.get("data") or {}).get("attributes") or {}
                price_str = attr.get("price_usd")
                if price_str:
                    price = float(price_str)
        except Exception:
            price = None

    if price and price > 0:
        _price_cache.set(f"price_usd:{mint}", price)

    return price


async def get_token_price_sol(mint: str) -> Optional[float]:
    """Precio actual de un token en SOL."""
    price_usd = await get_token_price_usd(mint)
    if price_usd is None:
        return None
    if mint == SOL_MINT:
        return 1.0
    sol_price = await get_sol_price_usd()
    if not sol_price or sol_price <= 0:
        return None
    return price_usd / sol_price


async def get_prices_usd_batch(mints) -> Dict[str, Optional[float]]:
    """Obtiene precios en USD para una lista de mints (con caché)."""
    result = {}
    for mint in mints:
        result[mint] = await get_token_price_usd(mint)
    return result
