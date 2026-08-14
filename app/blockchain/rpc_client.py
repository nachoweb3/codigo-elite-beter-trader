import httpx
import base58
from struct import unpack
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import get_settings
from app.services.cache import TTLCache
from app.services.http import with_retries
from app.services.rate_limit import helius_limiter

settings = get_settings()


class SolanaRPCClient:
    """Client para interactuar con Solana RPC"""

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url or settings.helius_rpc_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self._balance_cache = TTLCache(default_ttl=settings.CACHE_TTL)

    async def close(self):
        await self.client.aclose()

    async def _call(self, method: str, params: List[Any] = None) -> Dict:
        """Realiza una llamada RPC al nodo de Solana con reintentos"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or []
        }

        async def _do_call():
            async with helius_limiter:
                response = await self.client.post(
                    self.rpc_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                data = response.json()

                if "error" in data:
                    raise Exception(f"RPC Error: {data['error']}")

                return data.get("result", {})

        return await with_retries(
            _do_call,
            retries=3,
            base_delay=0.5,
            max_delay=3.0,
            label=f"rpc.{method}",
        )

    async def get_balance(self, wallet_address: str) -> float:
        """Obtiene el balance de SOL de una wallet (con caché TTL)"""
        cached = self._balance_cache.get(wallet_address)
        if cached is not None:
            return cached

        result = await self._call("getBalance", [wallet_address])
        balance = result.get("value", 0) / 1e9  # Convertir lamports a SOL

        self._balance_cache.set(wallet_address, balance)
        return balance

    async def get_signatures(
        self,
        wallet_address: str,
        limit: int = 100,
        before: Optional[str] = None
    ) -> List[Dict]:
        """Obtiene las firmas de transacciones de una wallet"""
        params = [wallet_address, {"limit": limit}]
        if before:
            params[1]["before"] = before

        result = await self._call("getSignaturesForAddress", params)
        return result if isinstance(result, list) else []

    async def get_transaction(self, signature: str) -> Optional[Dict]:
        """Obtiene los detalles de una transacción"""
        try:
            result = await self._call(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            )
            return result
        except Exception as e:
            print(f"Error fetching transaction {signature}: {e}")
            return None

    async def get_account_info(self, account_address: str) -> Optional[Dict]:
        """Obtiene información de una cuenta"""
        try:
            result = await self._call(
                "getAccountInfo",
                [account_address, {"encoding": "jsonParsed"}]
            )
            return result.get("value")
        except Exception:
            return None

    async def get_token_accounts(self, wallet_address: str) -> List[Dict]:
        """Obtiene todos los token accounts de una wallet"""
        try:
            result = await self._call(
                "getTokenAccountsByOwner",
                [wallet_address, {"programId": settings.WSOL_MINT}, {"encoding": "jsonParsed"}]
            )

            if isinstance(result, dict) and "value" in result:
                return result["value"]
            return []
        except Exception as e:
            print(f"Error getting token accounts: {e}")
            return []

    async def get_all_token_accounts(self, wallet_address: str) -> List[Dict]:
        """Obtiene todos los tokens SPL de una wallet"""
        try:
            result = await self._call(
                "getTokenAccountsByOwner",
                [wallet_address, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}]
            )

            if isinstance(result, dict) and "value" in result:
                return result["value"]
            return []
        except Exception as e:
            print(f"Error getting all token accounts: {e}")
            return []

    async def get_token_balances(self, wallet_address: str) -> List[Dict]:
        """Obtiene balances de tokens SPL vía RPC (fallback sin Helius premium).

        Retorna lista de dicts: {mint, decimals, amount, amount_raw}
        """
        accounts = await self.get_all_token_accounts(wallet_address)
        tokens = []

        for acc in accounts:
            try:
                parsed = acc["account"]["data"]["parsed"]["info"]
                mint = parsed.get("mint")
                token_amount = parsed.get("tokenAmount", {})
                amount = float(token_amount.get("uiAmount") or 0)
                decimals = int(token_amount.get("decimals", 9))
                raw = int(float(token_amount.get("amount", 0) or 0))

                if mint and amount > 0:
                    tokens.append({
                        "mint": mint,
                        "decimals": decimals,
                        "amount": amount,
                        "amount_raw": raw,
                    })
            except Exception:
                continue

        tokens.sort(key=lambda t: t["amount"], reverse=True)
        return tokens

    @staticmethod
    def is_valid_address(address: str) -> bool:
        """Verifica si una dirección de Solana es válida"""
        try:
            base58.b58decode(address)
            return len(address) >= 32 and len(address) <= 44
        except Exception:
            return False

    @staticmethod
    def decode_instruction_data(data: str) -> Dict[str, Any]:
        """Decodifica los datos de instrucción de una transacción"""
        try:
            raw_data = base58.b58decode(data)
            if len(raw_data) < 8:
                return {"instruction_type": "unknown"}

            # Los primeros 8 bytes son el discriminator (para programas Anchor)
            discriminator = raw_data[:8]
            instruction_data = raw_data[8:] if len(raw_data) > 8 else b""

            return {
                "discriminator": discriminator.hex(),
                "data_length": len(instruction_data),
                "instruction_type": "anchor" if len(instruction_data) >= 0 else "unknown"
            }
        except Exception:
            return {"instruction_type": "unknown"}


# Cache simple para transacciones recientes
_transaction_cache = {}


def cache_transaction(signature: str, data: Dict):
    """Guarda una transacción en caché"""
    _transaction_cache[signature] = data


def get_cached_transaction(signature: str) -> Optional[Dict]:
    """Obtiene una transacción de caché"""
    return _transaction_cache.get(signature)
