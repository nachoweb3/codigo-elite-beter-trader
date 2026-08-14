import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import get_settings
from app.models.schemas import Transaction, TransactionType

settings = get_settings()


class HeliusClient:
    """Cliente para Helius API - mejor para obtener transacciones de tokens"""

    def __init__(self):
        self.api_url = settings.helius_api_url
        self.api_key = settings.helius_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def get_token_transfers(
        self,
        wallet_address: str,
        limit: int = 100
    ) -> List[Dict]:
        """Obtiene las transferencias de tokens usando Helius API"""

        # Usar el endpoint de webhooks de Helius para obtener transacciones
        url = f"{self.api_url}/api/v0/transactions"

        params = {
            "api-key": self.api_key,
            "account": wallet_address,
            "limit": limit
        }

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list):
                return data
            return data.get("result", []) if isinstance(data, dict) else []
        except Exception as e:
            print(f"Error fetching token transfers: {e}")
            return []

    async def parse_transaction(self, tx_data: Dict, wallet_address: str) -> List[Transaction]:
        """Parsea una transacción de Helius y extrae operaciones relevantes"""
        transactions = []

        if not tx_data or "type" not in tx_data:
            return transactions

        tx_type = tx_data.get("type", "UNKNOWN")
        timestamp = tx_data.get("timestamp")
        if timestamp:
            timestamp = datetime.fromtimestamp(timestamp)
        else:
            timestamp = datetime.now()

        signature = tx_data.get("signature", "")
        slot = tx_data.get("slot")

        # Datos de la transacción
        native_transfers = tx_data.get("nativeTransfers", []) or []
        token_transfers = tx_data.get("tokenTransfers", []) or []

        # Procesar transferencias de tokens
        for transfer in token_transfers:
            from_address = transfer.get("fromUserAccount")
            to_address = transfer.get("toUserAccount")
            token_amount = float(transfer.get("tokenAmount", 0))
            mint = transfer.get("mintAddress")

            # Determinar tipo
            if from_address == wallet_address:
                tx_type = TransactionType.SELL
            elif to_address == wallet_address:
                tx_type = TransactionType.BUY
            else:
                continue

            # Obtener símbolo del token
            token_info = transfer.get("tokenAmount", {})
            decimals = token_info.get("decimals", 6)
            amount_float = token_amount / (10 ** decimals) if decimals > 0 else token_amount

            # Calcular valor en SOL (aproximado)
            sol_amount = transfer.get("nativeTransferred", 0) / 1e9

            transactions.append(Transaction(
                signature=signature,
                timestamp=timestamp,
                type=tx_type,
                token_address=mint,
                token_symbol=transfer.get("tokenSymbol") or self._get_token_symbol(mint),
                token_amount=amount_float,
                sol_amount=abs(sol_amount) if sol_amount != 0 else amount_float * 0.001,  # Fallback
                price_per_token=sol_amount / amount_float if amount_float > 0 else 0,
                fee=tx_data.get("fee", 0) / 1e9,
                slot=slot
            ))

        # Si no hay transferencias de tokens, buscar en cambios de balance
        if not transactions and token_transfers:
            for transfer in token_transfers:
                mint = transfer.get("mintAddress")
                from_address = transfer.get("fromUserAccount")
                to_address = transfer.get("toUserAccount")

                if from_address == wallet_address or to_address == wallet_address:
                    token_amount = float(transfer.get("tokenAmount", 0))
                    token_info = transfer.get("tokenAmount", {})
                    decimals = token_info.get("decimals", 6)
                    amount_float = token_amount / (10 ** decimals)

                    transactions.append(Transaction(
                        signature=signature,
                        timestamp=timestamp,
                        type=TransactionType.SWAP,
                        token_address=mint,
                        token_symbol=transfer.get("tokenSymbol") or self._get_token_symbol(mint),
                        token_amount=amount_float,
                        sol_amount=amount_float * 0.001,  # Valor estimado
                        price_per_token=0.001,
                        fee=tx_data.get("fee", 0) / 1e9,
                        slot=slot
                    ))

        return transactions

    async def get_parsed_transactions(
        self,
        wallet_address: str,
        limit: int = 100
    ) -> List[Transaction]:
        """Obtiene y parsea todas las transacciones de una wallet"""
        all_transactions = []

        try:
            # Obtener transacciones desde Helius
            url = f"{self.api_url}/api/v0/addresses/{wallet_address}/transactions"

            params = {
                "api-key": self.api_key
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Helius retorna un array de transacciones
            txs = data if isinstance(data, list) else data.get("result", [])

            for tx_data in txs[:limit]:
                parsed = await self.parse_transaction(tx_data, wallet_address)
                all_transactions.extend(parsed)

        except Exception as e:
            print(f"Error in get_parsed_transactions: {e}")
            # Fallback: intentar con RPC estándar
            pass

        return all_transactions

    @staticmethod
    def _get_token_symbol(mint_address: str) -> str:
        """Obtiene símbolo de token (mapa básico)"""
        known_tokens = {
            "So11111111111111111111111111111111111111112": "SOL",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        }
        return known_tokens.get(mint_address, "TOKEN")
