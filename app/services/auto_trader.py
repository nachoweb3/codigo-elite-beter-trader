"""
Servicio de Trading Automatizado para Solana
Integra con Jupiter API para ejecutar swaps automáticamente
"""
import httpx
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import base64
import json
from app.config import get_settings

# Solana imports
from solders.pubkey import Pubkey
from solders.keypair import Keypair as SoldersKeypair
from solders.signature import Signature
from solana.rpc.async_api import AsyncClient

settings = get_settings()


class TradeType(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StrategyType(str, Enum):
    DCA = "dca"  # Dollar Cost Averaging
    SIGNAL = "signal"  # Basado en señales
    GRID = "grid"  # Grid trading
    SCALP = "scalp"  # Scalping rápido


@dataclass
class Trade:
    id: str
    type: TradeType
    input_token: str
    output_token: str
    input_amount: float
    expected_output: float
    status: TradeStatus
    signature: Optional[str] = None
    error: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class Strategy:
    id: str
    name: str
    type: StrategyType
    wallet_address: str
    token_pair: str  # e.g., "SOL/BONK"
    is_active: bool
    config: Dict[str, Any]


class JupiterClient:
    """Cliente para interactuar con Jupiter API"""

    def __init__(self):
        self.base_url = "https://quote-api.jup.ag/v6"
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100  # 1% slippage
    ) -> Dict[str, Any]:
        """
        Obtiene un quote para el swap

        Args:
            input_mint: Mint address del token a vender
            output_mint: Mint address del token a comprar
            amount: Cantidad en unidades mínimas (lamports/smallest unit)
            slippage_bps: Slippage en basis points (100 = 1%)

        Returns:
            Dict con la información del quote
        """
        try:
            url = f"{self.base_url}/quote"
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": "false",
                "asLegacyTransaction": "false"
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            print(f"Error getting quote: {e}")
            return {}

    async def get_swap_transaction(
        self,
        quote_response: Dict,
        wallet_address: str,
        slippage_bps: int = 100
    ) -> Optional[str]:
        """
        Obtiene la transacción de swap serializada

        Returns:
            Transacción en base64 o None si falla
        """
        try:
            url = f"{self.base_url}/swap"
            params = {
                "quoteResponse": json.dumps(quote_response),
                "userPublicKey": wallet_address,
                "slippageBps": slippage_bps
            }

            response = await self.client.post(url, params=params)
            response.raise_for_status()
            data = response.json()

            return data.get("swapTransaction")

        except Exception as e:
            print(f"Error getting swap transaction: {e}")
            return None

    async def get_token_price(self, mint: str) -> Optional[float]:
        """Obtiene el precio de un token en USD"""
        try:
            # Usar SOL como input para obtener precio
            url = f"{self.base_url}/quote"
            params = {
                "inputMint": "So11111111111111111111111111111111111111112",  # SOL
                "outputMint": mint,
                "amount": 1000000000,  # 1 SOL
                "slippageBps": 100
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            out_amount = data.get("outAmount", 0)
            return float(out_amount) / 1000000000  # Normalizar

        except Exception as e:
            print(f"Error getting token price: {e}")
            return None


class AutoTrader:
    """
    Servicio de Trading Automatizado
    Gestiona la ejecución de trades y estrategias automáticas
    """

    # Direcciones de tokens comunes
    SOL = "So11111111111111111111111111111111111111112"
    USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

    def __init__(self):
        self.jupiter = JupiterClient()
        self.rpc_client = AsyncClient(settings.SOLANA_RPC_URL)
        self.active_strategies: Dict[str, Strategy] = {}
        self.trade_history: List[Trade] = []

    async def close(self):
        await self.jupiter.close()
        await self.rpc_client.close()

    async def execute_trade(
        self,
        wallet_keypair_bytes: bytes,
        input_mint: str,
        output_mint: str,
        amount: float,
        slippage_percent: float = 1.0
    ) -> Trade:
        """
        Ejecuta un trade (swap) en Solana

        Args:
            wallet_keypair_bytes: Keypair de la wallet (bytes)
            input_mint: Token a vender
            output_mint: Token a comprar
            amount: Cantidad a vender
            slippage_percent: Slippage máximo en porcentaje

        Returns:
            Trade con el resultado
        """
        import uuid

        trade_id = str(uuid.uuid4())
        # Crear keypair desde bytes usando solders
        keypair = SoldersKeypair.from_bytes(wallet_keypair_bytes)
        wallet_address = str(keypair.pubkey())

        # Determinar tipo de trade
        if input_mint == self.SOL:
            trade_type = TradeType.BUY
        elif output_mint == self.SOL:
            trade_type = TradeType.SELL
        else:
            trade_type = TradeType.SWAP

        trade = Trade(
            id=trade_id,
            type=trade_type,
            input_token=input_mint,
            output_token=output_mint,
            input_amount=amount,
            expected_output=0,
            status=TradeStatus.PENDING
        )

        try:
            # Obtener decimals del input token
            decimals = await self._get_token_decimals(input_mint)
            amount_smallest = int(amount * (10 ** decimals))

            # Obtener quote de Jupiter
            quote = await self.jupiter.get_quote(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount_smallest,
                slippage_bps=int(slippage_percent * 100)
            )

            if not quote or "outAmount" not in quote:
                trade.status = TradeStatus.FAILED
                trade.error = "No se pudo obtener quote"
                return trade

            trade.expected_output = float(quote["outAmount"]) / (10 ** await self._get_token_decimals(output_mint))

            # Obtener transacción de swap
            swap_txn_base64 = await self.jupiter.get_swap_transaction(
                quote_response=quote,
                wallet_address=wallet_address,
                slippage_bps=int(slippage_percent * 100)
            )

            if not swap_txn_base64:
                trade.status = TradeStatus.FAILED
                trade.error = "No se pudo obtener transacción"
                return trade

            # Deserializar y firmar transacción con nueva API
            # Usar la versión moderna de solana-py
            from solders.transaction import Transaction as SoldersTransaction

            swap_txn = SoldersTransaction.deserialize(base64.b64decode(swap_txn_base64))
            swap_txn.sign(keypair)

            # Enviar transacción
            from datetime import datetime
            result = await self.rpc_client.send_raw_transaction(
                swap_txn.serialize(),
                opts={"skip_preflight": False}
            )

            if result.value:
                trade.status = TradeStatus.EXECUTED
                trade.signature = str(result.value)
                trade.timestamp = datetime.now().isoformat()
            else:
                trade.status = TradeStatus.FAILED
                trade.error = "No se recibió signature"

        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.error = str(e)

        self.trade_history.append(trade)
        return trade

    async def get_best_trade_path(
        self,
        input_mint: str,
        output_mint: str,
        amount: float
    ) -> Dict[str, Any]:
        """
        Analiza la mejor ruta para un trade

        Returns:
            Dict con información sobre el trade esperado
        """
        try:
            decimals = await self._get_token_decimals(input_mint)
            amount_smallest = int(amount * (10 ** decimals))

            quote = await self.jupiter.get_quote(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount_smallest,
                slippage_bps=100
            )

            if not quote:
                return {"error": "No se pudo obtener quote"}

            output_decimals = await self._get_token_decimals(output_mint)

            return {
                "input_mint": input_mint,
                "output_mint": output_mint,
                "input_amount": amount,
                "expected_output": float(quote["outAmount"]) / (10 ** output_decimals),
                "price_impact_pct": quote.get("priceImpactPct", 0),
                "route": quote.get("routePlan", []),
                "market_infos": quote.get("marketInfos", [])
            }

        except Exception as e:
            return {"error": str(e)}

    async def _get_token_decimals(self, mint: str) -> int:
        """Obtiene los decimals de un token"""
        known_decimals = {
            self.SOL: 9,
            self.USDC: 6,
            self.USDT: 6,
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 5,  # BONK
            "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": 6,  # JUP
        }

        if mint in known_decimals:
            return known_decimals[mint]

        # Default a 9 para tokens desconocidos
        return 9

    def create_strategy(
        self,
        name: str,
        strategy_type: StrategyType,
        wallet_address: str,
        token_pair: str,
        config: Dict[str, Any]
    ) -> Strategy:
        """Crea una nueva estrategia de trading"""
        import uuid

        strategy = Strategy(
            id=str(uuid.uuid4()),
            name=name,
            type=strategy_type,
            wallet_address=wallet_address,
            token_pair=token_pair,
            is_active=True,
            config=config
        )

        self.active_strategies[strategy.id] = strategy
        return strategy

    def get_strategies(self, wallet_address: str = None) -> List[Strategy]:
        """Obtiene estrategias activas"""
        strategies = list(self.active_strategies.values())
        if wallet_address:
            strategies = [s for s in strategies if s.wallet_address == wallet_address]
        return strategies

    def get_trade_history(self, wallet_address: str = None) -> List[Trade]:
        """Obtiene historial de trades"""
        return self.trade_history


# Instancia global
auto_trader = AutoTrader()
