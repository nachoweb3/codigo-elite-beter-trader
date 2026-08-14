"""Pruebas de las protecciones del endpoint de trading."""
import pytest
from fastapi import HTTPException

from app.api import trading as trading_api
from app.models.schemas import TradeRequest


@pytest.mark.asyncio
async def test_server_side_trading_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(trading_api.settings, "ALLOW_SERVER_SIDE_TRADING", False)
    request = TradeRequest(
        private_key="never-used",
        input_token="So11111111111111111111111111111111111111112",
        output_token="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        amount=0.01,
    )

    with pytest.raises(HTTPException) as error:
        await trading_api.execute_trade(request)

    assert error.value.status_code == 403
    assert "private key" not in error.value.detail.lower()
