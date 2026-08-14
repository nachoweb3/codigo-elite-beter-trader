"""
Parser de transacciones usando la API REST de Helius
Esta API retorna datos ya parseados con tokenTransfers
"""
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.models.schemas import Transaction, TransactionType
from app.config import get_settings
from app.services.token_metadata import token_metadata_service
from app.services.rate_limit import helius_limiter
from app.services.http import with_retries

settings = get_settings()


class HeliusAPI:
    """Cliente para la API REST de Helius"""

    def __init__(self):
        self.api_key = settings.helius_api_key
        self.base_url = settings.helius_api_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.token_metadata = {}

    async def close(self):
        await self.client.aclose()

    async def get_transactions(
        self,
        wallet_address: str,
        limit: int = 100
    ) -> List[Dict]:
        """Obtiene transacciones de una wallet"""
        url = f"{self.base_url}/v0/addresses/{wallet_address}/transactions"

        params = {
            "api-key": self.api_key
        }

        try:
            async with helius_limiter:
                response = await with_retries(
                    lambda: self.client.get(url, params=params),
                    retries=3,
                    base_delay=0.6,
                    label=f"helius txns {wallet_address[:8]}",
                )
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list):
                return data[:limit]
            return []

        except Exception as e:
            print(f"Error fetching transactions: {e}")
            return []

    async def get_token_info(self, mint: str) -> Dict[str, Any]:
        """Obtiene información completa de un token (símbolo, nombre, logo).

        Usa la API DAS de Helius (getAsset) vía el servicio de metadatos,
        que además aplica caché TTL y fallback a tokens conocidos.
        """
        if mint in self.token_metadata:
            return self.token_metadata[mint]

        token_info = await token_metadata_service.get_token_metadata(mint)
        self.token_metadata[mint] = token_info
        return token_info

    async def parse_swap_transaction(
        self,
        tx_data: Dict,
        wallet_address: str
    ) -> List[Transaction]:
        """
        Parsea una transacción de swap de Helius
        Retorna una lista de transacciones (buy/sell)
        """
        transactions = []

        tx_type = tx_data.get("type", "")
        signature = tx_data.get("signature", "")
        timestamp = datetime.fromtimestamp(tx_data.get("timestamp", 0)) if tx_data.get("timestamp") else datetime.now()
        slot = tx_data.get("slot")
        fee = tx_data.get("fee", 0) / 1e9  # Convertir lamports a SOL

        # Procesar SWAP y TRANSFER con tokenTransfers (los swaps de agregadores
        # a veces vienen clasificados como TRANSFER/UNKNOWN).
        if tx_type not in ["SWAP", "TRANSFER"]:
            return transactions

        # Obtener transferencias de token
        token_transfers = tx_data.get("tokenTransfers", [])

        # Si es un swap con tokenTransfers
        if token_transfers:
            # SOL envuelto y stablecoins (el "dinero" que paga/recibe)
            wsol_mint = "So11111111111111111111111111111111111111112"
            stablecoins = {
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
            }

            # Tokens reales (no SOL ni stablecoin) que entran/salen de la wallet
            incoming = []  # Tokens que entran a la wallet
            outgoing = []  # Tokens que salen de la wallet

            # SOL total que entra/sale (tokenTransfers de WSOL + nativeTransfers)
            sol_in = 0.0
            sol_out = 0.0
            # Stablecoins que entran/salen (pago en USDC/USDT)
            stable_in = 0.0
            stable_out = 0.0

            for transfer in token_transfers:
                to_user = transfer.get("toUserAccount")
                from_user = transfer.get("fromUserAccount")
                amount = float(transfer.get("tokenAmount", 0) or 0)
                mint = transfer.get("mint")

                if mint == wsol_mint:
                    if to_user == wallet_address:
                        sol_in += amount
                    if from_user == wallet_address:
                        sol_out += amount
                elif mint in stablecoins:
                    if to_user == wallet_address:
                        stable_in += amount
                    if from_user == wallet_address:
                        stable_out += amount
                else:
                    if to_user == wallet_address:
                        incoming.append({"mint": mint, "amount": amount})
                    if from_user == wallet_address:
                        outgoing.append({"mint": mint, "amount": amount})

            # El SOL nativo también entra/sale (nativeTransfers):
            # en muchas ventas el SOL llega como lamports, no como WSOL token.
            for transfer in (tx_data.get("nativeTransfers") or []):
                nat_amount = transfer.get("amount", 0) / 1e9
                if transfer.get("toUserAccount") == wallet_address:
                    sol_in += nat_amount
                if transfer.get("fromUserAccount") == wallet_address:
                    sol_out += nat_amount

            async def _sol_equivalent(amount_usd: float) -> float:
                """Convierte un pago en stablecoins a SOL usando el precio actual."""
                if amount_usd <= 0:
                    return 0.0
                try:
                    from app.services.market import get_sol_price_usd
                    price = await get_sol_price_usd()
                except Exception:
                    price = 0.0
                return amount_usd / price if price and price > 0 else amount_usd

            # Compra: token real entra, se paga con SOL o stablecoin
            if incoming and (sol_out > 0 or stable_out > 0):
                # El valor pagado en SOL (WSOL/nativo + stablecoin convertida)
                if sol_out > 0:
                    paid_sol = sol_out
                else:
                    paid_sol = await _sol_equivalent(stable_out)

                for token in incoming:
                    token_info = await self.get_token_info(token["mint"])

                    transactions.append(Transaction(
                        signature=signature,
                        timestamp=timestamp,
                        type=TransactionType.BUY,
                        token_address=token["mint"],
                        token_symbol=token_info["symbol"],
                        token_name=token_info.get("name", ""),
                        token_logo=token_info.get("logo"),
                        token_amount=token["amount"],
                        sol_amount=paid_sol,
                        price_per_token=paid_sol / token["amount"] if token["amount"] > 0 else 0,
                        fee=fee,
                        slot=slot
                    ))

            # Venta: token real sale, se recibe SOL o stablecoin
            elif outgoing and (sol_in > 0 or stable_in > 0):
                if sol_in > 0:
                    received_sol = sol_in
                else:
                    received_sol = await _sol_equivalent(stable_in)

                for token in outgoing:
                    token_info = await self.get_token_info(token["mint"])

                    transactions.append(Transaction(
                        signature=signature,
                        timestamp=timestamp,
                        type=TransactionType.SELL,
                        token_address=token["mint"],
                        token_symbol=token_info["symbol"],
                        token_name=token_info.get("name", ""),
                        token_logo=token_info.get("logo"),
                        token_amount=token["amount"],
                        sol_amount=received_sol,
                        price_per_token=received_sol / token["amount"] if token["amount"] > 0 else 0,
                        fee=fee,
                        slot=slot
                    ))

        # Si es una transferencia de SOL (no swap)
        elif tx_type == "TRANSFER" and not token_transfers:
            native_transfers = tx_data.get("nativeTransfers", [])

            for transfer in native_transfers:
                to_user = transfer.get("toUserAccount")
                from_user = transfer.get("fromUserAccount")
                amount = transfer.get("amount", 0) / 1e9  # Convertir lamports

                if to_user == wallet_address and amount > 0:
                    transactions.append(Transaction(
                        signature=signature,
                        timestamp=timestamp,
                        type=TransactionType.TRANSFER_IN,
                        token_address=wsol_mint,
                        token_symbol="SOL",
                        token_name="Wrapped SOL",
                        token_logo="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
                        token_amount=amount,
                        sol_amount=amount,
                        price_per_token=1.0,
                        fee=0,
                        slot=slot
                    ))
                elif from_user == wallet_address and amount > 0:
                    transactions.append(Transaction(
                        signature=signature,
                        timestamp=timestamp,
                        type=TransactionType.TRANSFER_OUT,
                        token_address=wsol_mint,
                        token_symbol="SOL",
                        token_name="Wrapped SOL",
                        token_logo="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
                        token_amount=amount,
                        sol_amount=amount,
                        price_per_token=1.0,
                        fee=fee,
                        slot=slot
                    ))

        return transactions

    async def get_wallet_token_balances(self, wallet_address: str) -> List[Dict]:
        """
        Obtiene los balances de tokens de la wallet
        Retorna lista con información completa de cada token
        """
        try:
            url = f"{self.base_url}/v0/addresses/{wallet_address}/balances"
            params = {"api-key": self.api_key}

            async with helius_limiter:
                response = await with_retries(
                    lambda: self.client.get(url, params=params),
                    retries=3,
                    base_delay=0.6,
                    label=f"helius balances {wallet_address[:8]}",
                )
            response.raise_for_status()
            data = response.json()

            tokens = []

            if isinstance(data, dict) and "tokens" in data:
                for token_data in data.get("tokens", []):
                    mint = token_data.get("mint", "")
                    amount = token_data.get("amount", 0)
                    decimals = token_data.get("decimals", 9)

                    # Convertir amount según decimals
                    actual_amount = amount / (10 ** decimals) if amount > 0 else 0

                    # Obtener info completa del token
                    token_info = await self.get_token_info(mint)

                    if actual_amount > 0:
                        tokens.append({
                            "mint": mint,
                            "symbol": token_info["symbol"],
                            "name": token_info.get("name", ""),
                            "logo": token_info.get("logo"),
                            "decimals": decimals,
                            "amount": actual_amount,
                            "amount_raw": amount
                        })

            # Ordenar por cantidad
            tokens.sort(key=lambda x: x["amount"], reverse=True)

            return tokens

        except Exception as e:
            # Fallback: obtener balances vía RPC estándar (no requiere plan premium)
            print(f"Helius balances fallback para {wallet_address}: {e}")
            return await self._get_balances_via_rpc(wallet_address)

    async def _get_balances_via_rpc(self, wallet_address: str) -> List[Dict]:
        """Obtiene balances de tokens usando RPC estándar como fallback"""
        from app.blockchain.rpc_client import SolanaRPCClient

        rpc_client = SolanaRPCClient()
        try:
            balances = await rpc_client.get_token_balances(wallet_address)
            tokens = []

            for balance in balances:
                token_info = await self.get_token_info(balance["mint"])
                tokens.append({
                    "mint": balance["mint"],
                    "symbol": token_info["symbol"],
                    "name": token_info.get("name", ""),
                    "logo": token_info.get("logo"),
                    "decimals": balance["decimals"],
                    "amount": balance["amount"],
                    "amount_raw": balance["amount_raw"]
                })

            return tokens
        finally:
            await rpc_client.close()
