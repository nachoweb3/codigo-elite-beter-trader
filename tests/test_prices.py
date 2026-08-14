"""Tests del servicio de precios en tiempo real (sin red)."""
import asyncio

import pytest

from app.services.prices import get_token_price_usd, get_token_price_sol, KNOWN_TOKEN_PRICES

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestPrices:
    def test_known_stablecoin_price(self, monkeypatch):
        """USDC siempre cotiza ~1 USD sin llamadas de red."""
        monkeypatch.setattr("app.services.prices._price_cache", type("C", (), {"get": lambda self, k: None, "set": lambda self, k, v: None})())
        price = _run(get_token_price_usd(USDC))
        assert price == pytest.approx(1.0)

    def test_unknown_token_uses_dexscreener(self, monkeypatch):
        """Token no conocido usa DexScreener."""
        async def fake_get(url):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"pairs": [{"priceUsd": "0.000002236", "volume": {"h24": 100}}]}
            return R()

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **k):
                return await fake_get(url)

        import app.services.prices as prices_mod
        monkeypatch.setattr(prices_mod, "httpx", type("H", (), {"AsyncClient": FakeClient})())
        monkeypatch.setattr("app.services.prices._price_cache", type("C", (), {"get": lambda self, k: None, "set": lambda self, k, v: None})())

        price = _run(get_token_price_usd(BONK))
        assert price == pytest.approx(0.000002236)

    def test_price_in_sol(self, monkeypatch):
        """Convierte precio USD a SOL usando el precio de SOL."""
        # get_sol_price_usd se importa por nombre dentro de prices.py,
        # así que hay que parchear la referencia interna del módulo.
        async def fake_sol_price():
            return 100.0
        monkeypatch.setattr("app.services.prices.get_sol_price_usd", fake_sol_price)
        monkeypatch.setattr("app.services.prices._price_cache", type("C", (), {"get": lambda self, k: None, "set": lambda self, k, v: None})())

        async def fake_usd(mint):
            return 0.5  # 0.5 USD
        monkeypatch.setattr("app.services.prices.get_token_price_usd", fake_usd)

        price = _run(get_token_price_sol("SOMEMINT"))
        assert price == pytest.approx(0.005)
