"""
API Endpoints para Trading Automatizado
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from app.models.schemas import (
    TradeRequest, TradeResponse, QuoteRequest,
    StrategyRequest, StrategyResponse
)
from app.services.auto_trader import auto_trader, StrategyType, TradeStatus
from app.config import get_settings
from base58 import b58decode
import json

router = APIRouter(prefix="/api/trading", tags=["Trading"])
settings = get_settings()


@router.post("/quote")
async def get_trade_quote(request: QuoteRequest):
    """
    Obtiene un quote para un trade sin ejecutarlo

    Útil para mostrar al usuario qué obtendrá antes de confirmar
    """
    try:
        path_info = await auto_trader.get_best_trade_path(
            input_mint=request.input_token,
            output_mint=request.output_token,
            amount=request.amount
        )

        if "error" in path_info:
            raise HTTPException(status_code=400, detail=path_info["error"])

        return {
            "success": True,
            "input_token": request.input_token,
            "output_token": request.output_token,
            "input_amount": request.amount,
            "expected_output": path_info["expected_output"],
            "price_impact_pct": path_info.get("price_impact_pct", 0),
            "route": path_info.get("route", []),
            "execution_price": path_info["expected_output"] / request.amount if request.amount > 0 else 0
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute", response_model=TradeResponse)
async def execute_trade(request: TradeRequest):
    """
    Ejecuta un trade (swap) en Solana

    La ejecución server-side está desactivada por defecto. Nunca actives esta
    ruta con una private key si no tienes un gestor de secretos y aislamiento
    operativo revisados; el flujo recomendado firma localmente en la wallet.
    """
    if not settings.ALLOW_SERVER_SIDE_TRADING:
        raise HTTPException(
            status_code=403,
            detail="Ejecución server-side desactivada por seguridad. Firma el swap localmente con Phantom o Solflare.",
        )

    try:
        # Decodificar private key de base58 a bytes
        try:
            private_key_bytes = b58decode(request.private_key)
        except Exception as e:
            raise HTTPException(status_code=400, detail="Private key inválida")

        # Ejecutar trade
        trade = await auto_trader.execute_trade(
            wallet_keypair_bytes=private_key_bytes,
            input_mint=request.input_token,
            output_mint=request.output_token,
            amount=request.amount,
            slippage_percent=request.slippage_percent
        )

        return TradeResponse(
            trade_id=trade.id,
            type=trade.type.value,
            input_token=trade.input_token,
            output_token=trade.output_token,
            input_amount=trade.input_amount,
            expected_output=trade.expected_output,
            status=trade.status.value,
            signature=trade.signature,
            error=trade.error,
            timestamp=trade.timestamp
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-history")
async def get_trade_history(
    wallet_address: Optional[str] = Query(None, description="Filtrar por wallet")
):
    """Obtiene el historial de trades"""
    try:
        trades = auto_trader.get_trade_history(wallet_address)

        return {
            "success": True,
            "trades": [
                {
                    "trade_id": t.id,
                    "type": t.type.value,
                    "input_token": t.input_token,
                    "output_token": t.output_token,
                    "input_amount": t.input_amount,
                    "expected_output": t.expected_output,
                    "status": t.status.value,
                    "signature": t.signature,
                    "error": t.error,
                    "timestamp": t.timestamp
                }
                for t in trades
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strategies", response_model=StrategyResponse)
async def create_strategy(request: StrategyRequest):
    """
    Crea una nueva estrategia de trading automático

    Tipos de estrategia:
    - dca: Dollar Cost Averaging (compras periódicas)
    - signal: Basado en señales del mercado
    - grid: Grid trading (compras/ventas en rangos)
    - scalp: Scalping rápido
    """
    try:
        # Validar strategy type
        try:
            strategy_type = StrategyType(request.strategy_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de estrategia inválida. Opciones: {[t.value for t in StrategyType]}"
            )

        strategy = auto_trader.create_strategy(
            name=request.name,
            strategy_type=strategy_type,
            wallet_address=request.wallet_address,
            token_pair=request.token_pair,
            config=request.config
        )

        return StrategyResponse(
            id=strategy.id,
            name=strategy.name,
            type=strategy.type.value,
            wallet_address=strategy.wallet_address,
            token_pair=strategy.token_pair,
            is_active=strategy.is_active,
            config=strategy.config
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategies")
async def get_strategies(
    wallet_address: Optional[str] = Query(None, description="Filtrar por wallet")
):
    """Obtiene todas las estrategias activas"""
    try:
        strategies = auto_trader.get_strategies(wallet_address)

        return {
            "success": True,
            "strategies": [
                {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type.value,
                    "wallet_address": s.wallet_address,
                    "token_pair": s.token_pair,
                    "is_active": s.is_active,
                    "config": s.config
                }
                for s in strategies
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str):
    """Detiene una estrategia activa"""
    try:
        if strategy_id not in auto_trader.active_strategies:
            raise HTTPException(status_code=404, detail="Estrategia no encontrada")

        auto_trader.active_strategies[strategy_id].is_active = False

        return {
            "success": True,
            "message": "Estrategia detenida",
            "strategy_id": strategy_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tokens/popular")
async def get_popular_tokens():
    """Retorna lista de tokens populares para trading"""
    # Mints y logos verificados (API DAS). El frontend muestra un avatar
    # con la inicial del símbolo cuando un logo no está disponible.
    popular_tokens = [
        {
            "mint": "So11111111111111111111111111111111111111112",
            "symbol": "SOL",
            "name": "Wrapped SOL",
            "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png"
        },
        {
            "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "symbol": "USDC",
            "name": "USD Coin",
            "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png"
        },
        {
            "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "symbol": "USDT",
            "name": "Tether USD",
            "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.svg"
        },
        {
            "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "symbol": "BONK",
            "name": "Bonk",
            "logo": "https://arweave.net/hQiPZOsRZXGXBJd_82PhVdlM_hACsT_q6wqwf5cSY7I"
        },
        {
            "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
            "symbol": "JUP",
            "name": "Jupiter",
            "logo": "https://static.jup.ag/jup/icon.png"
        },
        {
            "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
            "symbol": "WIF",
            "name": "dogwifhat",
            "logo": "https://bafkreibk3covs5ltyqxa272uodhculbr6kea6betidfwy3ajsav2vjzyum.ipfs.nftstorage.link"
        },
        {
            "mint": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
            "symbol": "POPCAT",
            "name": "Popcat",
            "logo": "https://arweave.net/A1etRNMKxhlNGTf-gNBtJ75QJJ4NJtbKh_UXQTlLXzI"
        }
    ]

    return {
        "success": True,
        "tokens": popular_tokens
    }


@router.get("/price/{token_mint}")
async def get_token_price(token_mint: str):
    """Obtiene el precio actual de un token"""
    try:
        price = await auto_trader.jupiter.get_token_price(token_mint)

        if price is None:
            raise HTTPException(status_code=404, detail="No se pudo obtener el precio")

        return {
            "success": True,
            "token_mint": token_mint,
            "price_usd": price
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
