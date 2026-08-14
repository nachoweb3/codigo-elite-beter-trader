"""
Servicio para obtener metadatos de tokens y balances de la wallet.

Estrategia por niveles, sin depender de una API key:
  1. Tokens conocidos (SOL, USDC, USDT) -> sin llamadas externas
  2. API DAS getAsset de Helius (solo si hay key configurada)
  3. Programa Token Metadata on-chain via RPC público (siempre disponible)
  4. Metadatos por defecto

El endpoint REST /v0/tokens/metadata de Helius fue deprecado (HTTP 410).
"""
import base64
import struct
import httpx
from typing import List, Dict, Any, Optional
from app.config import get_settings
from app.services.cache import TTLCache

settings = get_settings()

# Programa Token Metadata de Metaplex: guarda nombre/símbolo/URI de cada token on-chain
METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"

# TTL largo: los metadatos de tokens cambian muy raramente
METADATA_CACHE_TTL = 60 * 60 * 24  # 24 horas

# Tokens conocidos con URLs de logo verificadas (token-list oficial de Solana)
KNOWN_TOKENS: Dict[str, Dict[str, Any]] = {
    "So11111111111111111111111111111111111111112": {
        "mint": "So11111111111111111111111111111111111111112",
        "symbol": "SOL",
        "name": "Wrapped SOL",
        "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
        "logoURI": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
        "decimals": 9,
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "symbol": "USDC",
        "name": "USD Coin",
        "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png",
        "logoURI": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png",
        "decimals": 6,
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        "symbol": "USDT",
        "name": "Tether USD",
        "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.svg",
        "logoURI": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.svg",
        "decimals": 6,
    },
}


class TokenMetadataService:
    """Servicio para obtener metadatos de tokens sin depender de una API key."""

    def __init__(self):
        self.api_key = settings.helius_api_key
        self.rpc_url = settings.helius_rpc_url
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        self.metadata_cache = TTLCache(default_ttl=METADATA_CACHE_TTL)

    async def close(self):
        await self.client.aclose()

    async def get_token_metadata(self, mint_address: str) -> Dict[str, Any]:
        """Obtiene metadatos completos de un token (símbolo, nombre, logo)."""
        cached = self.metadata_cache.get(mint_address)
        if cached is not None:
            return cached

        # Nivel 1: tokens conocidos, sin llamadas externas
        if mint_address in KNOWN_TOKENS:
            info = KNOWN_TOKENS[mint_address]
            self.metadata_cache.set(mint_address, info)
            return info

        # Nivel 2: API DAS (requiere key de Helius, da mejor logo)
        if self.api_key:
            info = await self._fetch_das_metadata(mint_address)
            if info.get("symbol") != "TOKEN":
                self.metadata_cache.set(mint_address, info)
                return info

        # Nivel 3: on-chain vía RPC público (sin key, siempre disponible)
        info = await self._fetch_onchain_metadata(mint_address)
        if info.get("symbol") != "TOKEN":
            self.metadata_cache.set(mint_address, info)
            return info

        # Nivel 4: GeckoTerminal (sin key) - recupera símbolo/nombre de
        # tokens cuya metadata on-chain ya no está indexada (mints quemados).
        info = await self._fetch_geckoterminal_metadata(mint_address)
        self.metadata_cache.set(mint_address, info)
        return info

    async def _fetch_onchain_metadata(self, mint_address: str) -> Dict[str, Any]:
        """
        Lee el programa Token Metadata de Metaplex directamente de la blockchain
        usando el RPC público. No requiere API key.
        """
        try:
            from solders.pubkey import Pubkey

            mint = Pubkey.from_string(mint_address)
            program = Pubkey.from_string(METADATA_PROGRAM_ID)
            seeds = [b"metadata", bytes(program), bytes(mint)]
            pda, _ = Pubkey.find_program_address(seeds, program)

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [str(pda), {"encoding": "base64"}],
            }
            # Usar el RPC público (sin key) para no gastar la cuota de Helius
            response = await self.client.post(PUBLIC_RPC, json=payload, timeout=8.0)
            response.raise_for_status()
            data = response.json()

            account = (data.get("result") or {}).get("value")
            if not account:
                return self._get_default_metadata(mint_address)

            raw = base64.b64decode(account["data"][0])
            name, symbol, uri = self._parse_onchain_metadata(raw)
            if not symbol or symbol == "TOKEN":
                return self._get_default_metadata(mint_address)

            logo = await self._fetch_metadata_image(uri) if uri else None
            return {
                "mint": mint_address,
                "symbol": symbol,
                "name": name or "Unknown Token",
                "logo": logo,
                "logoURI": logo,
                "decimals": 9,
                "description": "",
            }

        except Exception as e:
            print(f"Error fetching on-chain metadata for {mint_address}: {e}")
            return self._get_default_metadata(mint_address)

    async def _fetch_geckoterminal_metadata(self, mint_address: str) -> Dict[str, Any]:
        """Consulta GeckoTerminal (gratis, sin key) para recuperar símbolo y nombre
        de tokens cuya metadata on-chain ya no está indexada.
        """
        try:
            url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint_address}"
            response = await self.client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            attributes = (data.get("data") or {}).get("attributes") or {}
            symbol = attributes.get("symbol") or ""
            name = attributes.get("name") or ""
            logo = attributes.get("image_url") or None
            if symbol and symbol != "TOKEN":
                return {
                    "mint": mint_address,
                    "symbol": symbol[:12],
                    "name": name or symbol,
                    "logo": logo,
                    "logoURI": logo,
                    "decimals": 9,
                    "description": "",
                }
        except Exception:
            pass
        return self._get_default_metadata(mint_address)

    @staticmethod
    def _parse_onchain_metadata(raw: bytes):
        """Parsea el account del programa Token Metadata.

        Layout: key(1) + update_authority(32) + mint(32) +
        name(u32 len + bytes) + symbol(u32 len + bytes) + uri(u32 len + bytes)
        """
        def clean(b: bytes) -> str:
            return b.decode("utf-8", "replace").rstrip("\x00").strip()

        off = 65  # key + update_authority + mint
        name_len = struct.unpack_from("<I", raw, off)[0]
        name = clean(raw[off + 4:off + 4 + name_len])
        off += 4 + name_len
        sym_len = struct.unpack_from("<I", raw, off)[0]
        symbol = clean(raw[off + 4:off + 4 + sym_len])
        off += 4 + sym_len
        uri_len = struct.unpack_from("<I", raw, off)[0]
        uri = clean(raw[off + 4:off + 4 + uri_len])
        return name, symbol, uri

    async def _fetch_metadata_image(self, uri: str) -> Optional[str]:
        """Obtiene la imagen desde el JSON de metadatos alojado en arweave/ipfs."""
        if not uri or not uri.startswith("http"):
            return None
        try:
            response = await self.client.get(uri, timeout=8.0)
            response.raise_for_status()
            metadata = response.json()
            return metadata.get("image") or metadata.get("image_uri")
        except Exception:
            # La imagen es opcional: el frontend muestra un avatar de respaldo
            return None

    async def _fetch_das_metadata(self, mint_address: str) -> Dict[str, Any]:
        """Consulta la API DAS getAsset de Helius para un mint."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": mint_address},
            }
            response = await self.client.post(self.rpc_url, json=payload, timeout=8.0)
            response.raise_for_status()
            data = response.json()

            result = data.get("result")
            if not result:
                return self._get_default_metadata(mint_address)

            content = result.get("content", {}) or {}
            metadata = content.get("metadata", {}) or {}
            links = content.get("links", {}) or {}
            token_info = result.get("token_info", {}) or {}

            logo = links.get("image")
            if not logo and content.get("files"):
                logo = content["files"][0].get("uri")

            return {
                "mint": mint_address,
                "symbol": metadata.get("symbol") or "TOKEN",
                "name": metadata.get("name") or "Unknown Token",
                "logo": logo,
                "logoURI": logo,
                "decimals": token_info.get("decimals", 9),
                "description": metadata.get("description", ""),
            }

        except Exception as e:
            print(f"Error fetching token metadata for {mint_address}: {e}")
            return self._get_default_metadata(mint_address)

    def _get_default_metadata(self, mint_address: str) -> Dict[str, Any]:
        """Retorna metadatos por defecto si no se pueden obtener.

        En vez de "TOKEN"/"Unknown Token" genérico, deriva un identificador
        legible del mint (mints quemados no tienen metadata en ninguna fuente).
        """
        if not mint_address:
            return {"mint": mint_address, "symbol": "TOKEN", "name": "Unknown Token",
                    "logo": None, "logoURI": None, "decimals": 9, "description": ""}
        # Símbolo derivado: primeras 3-4 letras alfanuméricas en mayúsculas
        letters = "".join(c for c in mint_address[:6] if c.isalpha())[:4].upper()
        symbol = letters if len(letters) >= 3 else "TKN"
        short = f"{mint_address[:4]}…{mint_address[-4:]}"
        return {
            "mint": mint_address,
            "symbol": symbol,
            "name": f"Token {short}",
            "logo": None,
            "logoURI": None,
            "decimals": 9,
            "description": "",
        }

    async def get_wallet_balances(self, wallet_address: str) -> List[Dict[str, Any]]:
        """
        Obtiene todos los balances de tokens de una wallet
        Incluye tokens SPL y SOL nativo
        """
        try:
            url = f"{settings.helius_api_url}/v0/addresses/{wallet_address}/balances"
            params = {
                "api-key": self.api_key
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            tokens = []

            # Procesar balances de tokens
            if isinstance(data, dict) and "tokens" in data:
                for token_data in data.get("tokens", []):
                    mint = token_data.get("mint", "")
                    amount = token_data.get("amount", 0)
                    decimals = token_data.get("decimals", 9)

                    # Obtener metadatos (con caché y fallback)
                    meta = await self.get_token_metadata(mint)

                    # Convertir amount según decimals
                    actual_amount = amount / (10 ** decimals) if amount > 0 else 0

                    if actual_amount > 0:
                        tokens.append({
                            "mint": mint,
                            "symbol": meta.get("symbol", "TOKEN"),
                            "name": meta.get("name", "Unknown Token"),
                            "logo": meta.get("logo"),
                            "decimals": decimals,
                            "amount": actual_amount,
                            "amount_raw": amount
                        })

            # Ordenar por cantidad
            tokens.sort(key=lambda x: x["amount"], reverse=True)

            return tokens

        except Exception as e:
            print(f"Error fetching wallet balances: {e}")
            return []

    async def get_portfolio_summary(self, wallet_address: str) -> Dict[str, Any]:
        """
        Obtiene un resumen completo del portfolio
        Incluye balance SOL y todos los tokens con información detallada
        """
        try:
            # Obtener balances
            balances = await self.get_wallet_balances(wallet_address)

            # Filtrar solo tokens con balance > 0
            active_tokens = [t for t in balances if t["amount"] > 0]

            return {
                "wallet": wallet_address,
                "total_tokens": len(active_tokens),
                "tokens": active_tokens[:50],  # Limitar a 50 tokens
                "timestamp": None
            }

        except Exception as e:
            print(f"Error getting portfolio summary: {e}")
            return {
                "wallet": wallet_address,
                "total_tokens": 0,
                "tokens": [],
                "error": str(e)
            }

    async def get_token_list_from_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """
        A partir de las transacciones, crea una lista de tokens únicos
        con metadatos completos
        """
        unique_mints = set()

        # Extraer todos los mint addresses de las transacciones
        for tx in transactions:
            token_transfers = tx.get("tokenTransfers", [])
            for transfer in token_transfers:
                mint = transfer.get("mint")
                if mint and mint != "So11111111111111111111111111111111111111112":
                    unique_mints.add(mint)

        # Obtener metadatos para cada token único
        tokens_metadata = []
        for mint in unique_mints:
            metadata = await self.get_token_metadata(mint)
            tokens_metadata.append(metadata)

        return tokens_metadata

    async def enrich_transaction_with_metadata(self, tx_data: Dict, wallet_address: str) -> List[Dict]:
        """
        Enriquece una transacción con metadatos completos de los tokens
        """
        enriched_transactions = []
        token_transfers = tx_data.get("tokenTransfers", [])

        for transfer in token_transfers:
            mint = transfer.get("mint")
            if not mint:
                continue

            # Obtener metadatos del token
            metadata = await self.get_token_metadata(mint)

            enriched_transactions.append({
                "signature": tx_data.get("signature", ""),
                "timestamp": tx_data.get("timestamp", 0),
                "slot": tx_data.get("slot"),
                "type": tx_data.get("type", ""),
                "fee": tx_data.get("fee", 0) / 1e9,
                "token": {
                    "mint": mint,
                    "symbol": metadata.get("symbol", "TOKEN"),
                    "name": metadata.get("name", "Unknown Token"),
                    "logo": metadata.get("logo"),
                    "decimals": metadata.get("decimals", 9),
                },
                "amount": transfer.get("tokenAmount", 0),
                "to_user": transfer.get("toUserAccount"),
                "from_user": transfer.get("fromUserAccount"),
                "direction": "in" if transfer.get("toUserAccount") == wallet_address else "out"
            })

        return enriched_transactions


# Instancia global (comparte la caché entre requests)
token_metadata_service = TokenMetadataService()
