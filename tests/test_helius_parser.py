"""Tests unitarios para el parser de swaps de Helius (sin red)."""
import asyncio

import pytest

from app.models.schemas import TransactionType
from app.services.helius_parser import HeliusAPI

WALLET = "5rqBo8rnbcGW6XTqRDpCZ6cAPpZjwYR9t5z4MMh1YRQ4"
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MEME = "FkD1S9QujAMouUyvHwowrvoq1uTaJs1WPvT3rby6pump"


def make_swap(token_transfers, native_transfers=None, tx_type="SWAP"):
    return {
        "type": tx_type,
        "signature": "sig123",
        "timestamp": 1700000000,
        "fee": 5000,
        "slot": 1,
        "tokenTransfers": token_transfers,
        "nativeTransfers": native_transfers or [],
    }


def _mock_api(monkeypatch):
    api = HeliusAPI()

    async def fake_token_info(mint):
        return {"symbol": "MEME", "name": "Meme", "logo": None}

    monkeypatch.setattr(api, "get_token_info", fake_token_info)
    # Precio de SOL fijo para convertir stablecoins (evita llamadas de red)
    async def fake_price():
        return 100.0
    monkeypatch.setattr("app.services.market.get_sol_price_usd", fake_price)
    return api


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSwapParser:
    def test_detects_buy_with_wsol(self, monkeypatch):
        """Compra pagando con WSOL (token entra, SOL sale)."""
        tx = make_swap([
            {"mint": WSOL, "tokenAmount": 10, "fromUserAccount": WALLET, "toUserAccount": "POOL"},
            {"mint": MEME, "tokenAmount": 1000, "fromUserAccount": "POOL", "toUserAccount": WALLET},
        ])
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))

        assert len(parsed) == 1
        assert parsed[0].type == TransactionType.BUY
        assert parsed[0].token_address == MEME
        assert parsed[0].token_amount == 1000
        assert parsed[0].sol_amount == 10

    def test_detects_sell_with_native_sol(self, monkeypatch):
        """Venta donde el SOL entra como nativeTransfers (no WSOL token).

        Este era el caso que el parser perdía: el SOL llega como lamports
        nativos, no como tokenTransfers de WSOL.
        """
        tx = make_swap([
            {"mint": MEME, "tokenAmount": 500, "fromUserAccount": WALLET, "toUserAccount": "POOL"},
        ], native_transfers=[
            {"amount": 5000000000, "fromUserAccount": "POOL", "toUserAccount": WALLET},  # 5 SOL
        ])
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))

        assert len(parsed) == 1
        assert parsed[0].type == TransactionType.SELL
        assert parsed[0].token_address == MEME
        assert parsed[0].token_amount == 500
        assert parsed[0].sol_amount == pytest.approx(5.0)

    def test_detects_sell_with_wsol_incoming(self, monkeypatch):
        """Venta clásica: token sale, WSOL entra como token transfer."""
        tx = make_swap([
            {"mint": MEME, "tokenAmount": 300, "fromUserAccount": WALLET, "toUserAccount": "POOL"},
            {"mint": WSOL, "tokenAmount": 3, "fromUserAccount": "POOL", "toUserAccount": WALLET},
        ])
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))

        assert len(parsed) == 1
        assert parsed[0].type == TransactionType.SELL
        assert parsed[0].sol_amount == pytest.approx(3.0)

    def test_detects_buy_paid_with_usdc(self, monkeypatch):
        """Compra pagando con USDC: USDC sale, token entra."""
        tx = make_swap([
            {"mint": USDC, "tokenAmount": 25, "fromUserAccount": WALLET, "toUserAccount": "POOL"},
            {"mint": MEME, "tokenAmount": 2000, "fromUserAccount": "POOL", "toUserAccount": WALLET},
        ])
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))

        assert len(parsed) == 1
        assert parsed[0].type == TransactionType.BUY
        # 25 USDC a 100 SOL/USD = 0.25 SOL
        assert parsed[0].sol_amount == pytest.approx(0.25, abs=0.01)

    def test_ignores_unknown_types(self, monkeypatch):
        """Tipos no soportados (UNKNOWN, COMPRESSED_NFT) no generan trades."""
        tx = make_swap([{"mint": MEME, "tokenAmount": 100, "fromUserAccount": WALLET, "toUserAccount": "X"}],
                       tx_type="UNKNOWN")
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))
        assert parsed == []

    def test_transfer_without_sol_still_parsed(self, monkeypatch):
        """TRANSFER con tokenTransfers (venta via agregador) se procesa igual."""
        tx = make_swap([
            {"mint": MEME, "tokenAmount": 100, "fromUserAccount": WALLET, "toUserAccount": "POOL"},
            {"mint": WSOL, "tokenAmount": 0.9, "fromUserAccount": "POOL", "toUserAccount": WALLET},
        ], tx_type="TRANSFER")
        api = _mock_api(monkeypatch)
        parsed = _run(api.parse_swap_transaction(tx, WALLET))

        assert len(parsed) == 1
        assert parsed[0].type == TransactionType.SELL
