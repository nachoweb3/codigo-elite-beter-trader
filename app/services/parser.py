"""
Parser profesional de transacciones de Solana para trading de memecoins
Soporta Jupiter, Raydium, Orca y otros DEXs principales
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from collections import defaultdict
from app.models.schemas import Transaction, TransactionType
from app.config import get_settings

settings = get_settings()


# Program IDs conocidos
JUPITER_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
JUPITER_LIMIT = "JUP4Fb2cqiRUcaTHdrPCcLHgYxRz9DPGU4mDCbHZnMKQ"
RAYDIUM_AMM = "675kPX9MHTjS2zt1qf1WNgJuw1MWgCPLHY4vcvwHbZZE"
RAYDIUM_V2 = "9W959DqEETiGZocYGoQkVjyJk8C8UAGkAzNfyiFw3zWg"
RAYDIUM_CLMM = "CAMMCzo5YL8w4VFF8KVHrK22GGUQpMpTFb5iJHJxgKWs"
ORCA_SWAP = "9W959DqEETiGZocYGoQkVjyJk8C8UAGkAzNfyiFw3zWg"
ORCA_V2 = "swapV2 FL1oxzq7jUmPzCR459bddVYmNKtMnm5ZPAfkbBqMs3"
ORCA_WHIRLPOOL = "whirLbMiicVdio4qvUfX5jdJiFaHutebx48qQPBotD"

# Tokens conocidos
KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": ("SOL", 9),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ("BONK", 5),
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": ("JUP", 6),
}


class SolanaTransactionParser:
    """Parser profesional de transacciones de Solana"""

    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    def parse_transaction(
        self,
        tx_data: Dict[str, Any],
        wallet_address: str
    ) -> List[Transaction]:
        """
        Parsea una transacción de Solana y extrae operaciones de trading
        Retorna una lista de transacciones detectadas
        """
        if not tx_data or tx_data.get("error"):
            return []

        meta = tx_data.get("meta", {})
        if not meta:
            return []

        transaction = tx_data.get("transaction", {})
        message = transaction.get("message", {})

        # Obtener información básica
        signature = tx_data.get("transaction", {}).get("signatures", [""])[0]
        block_time = tx_data.get("blockTime")
        timestamp = datetime.fromtimestamp(block_time) if block_time else datetime.now()
        slot = tx_data.get("slot")
        fee = meta.get("fee", 0) / 1e9  # Convertir lamports a SOL

        # Obtener cambios de balance
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        account_keys = self._extract_account_keys(message)

        # Buscar índice de la wallet
        wallet_indices = [i for i, key in enumerate(account_keys) if key == wallet_address]

        if not wallet_indices:
            return []

        wallet_index = wallet_indices[0]

        # Obtener cambios de token
        token_balances = meta.get("postTokenBalances", [])
        pre_token_balances = meta.get("preTokenBalances", [])

        # Parsear cambios de balance de SOL
        sol_change = (post_balances[wallet_index] - pre_balances[wallet_index]) / 1e9

        # Detectar tipo de transacción
        instructions = message.get("instructions", [])
        inner_instructions = meta.get("innerInstructions", [])

        # Buscar instrucciones de DEX
        dex_operations = self._parse_dex_instructions(
            instructions,
            inner_instructions,
            account_keys,
            wallet_address
        )

        # Si encontramos operaciones de DEX
        if dex_operations:
            transactions = []
            for op in dex_operations:
                transactions.append(Transaction(
                    signature=signature,
                    timestamp=timestamp,
                    type=op["type"],
                    token_address=op["token_address"],
                    token_symbol=op["token_symbol"],
                    token_amount=op["token_amount"],
                    sol_amount=op["sol_amount"],
                    price_per_token=op["price_per_token"],
                    fee=fee,
                    slot=slot
                ))
            return transactions

        # Si no es DEX, verificar si es transferencia de token
        token_transfers = self._parse_token_transfers(
            pre_token_balances,
            token_balances,
            wallet_address,
            account_keys
        )

        if token_transfers:
            transactions = []
            for transfer in token_transfers:
                transactions.append(Transaction(
                    signature=signature,
                    timestamp=timestamp,
                    type=transfer["type"],
                    token_address=transfer["mint"],
                    token_symbol=transfer["symbol"],
                    token_amount=transfer["amount"],
                    sol_amount=transfer.get("sol_amount", 0),
                    price_per_token=transfer.get("price", 0),
                    fee=fee,
                    slot=slot
                ))
            return transactions

        # Si solo hay cambio de SOL
        if abs(sol_change) > 0.000001:
            return [Transaction(
                signature=signature,
                timestamp=timestamp,
                type=TransactionType.TRANSFER_IN if sol_change > 0 else TransactionType.TRANSFER_OUT,
                token_address="So11111111111111111111111111111111111111112",
                token_symbol="SOL",
                token_amount=abs(sol_change),
                sol_amount=abs(sol_change),
                price_per_token=1.0,
                fee=fee,
                slot=slot
            )]

        return []

    def _extract_account_keys(self, message: Dict) -> List[str]:
        """Extrae las public keys de una transacción"""
        account_keys = message.get("accountKeys", [])

        # Manejar diferentes formatos
        keys = []
        for key in account_keys:
            if isinstance(key, str):
                keys.append(key)
            elif isinstance(key, dict):
                keys.append(key.get("pubkey", ""))

        return keys

    def _parse_dex_instructions(
        self,
        instructions: List[Dict],
        inner_instructions: List[Dict],
        account_keys: List[str],
        wallet_address: str
    ) -> List[Dict]:
        """Parsea instrucciones de DEX (Jupiter, Raydium, Orca)"""
        operations = []

        for instr in instructions:
            program_id = instr.get("programId")

            if program_id in [JUPITER_PROGRAM, JUPITER_LIMIT]:
                # Jupiter swap
                parsed = self._parse_jupiter_swap(
                    instr,
                    inner_instructions,
                    account_keys,
                    wallet_address
                )
                if parsed:
                    operations.append(parsed)

            elif program_id in [RAYDIUM_AMM, RAYDIUM_V2, RAYDIUM_CLMM]:
                # Raydium swap
                parsed = self._parse_raydium_swap(
                    instr,
                    inner_instructions,
                    account_keys,
                    wallet_address
                )
                if parsed:
                    operations.append(parsed)

            elif program_id in [ORCA_SWAP, ORCA_V2, ORCA_WHIRLPOOL]:
                # Orca swap
                parsed = self._parse_orca_swap(
                    instr,
                    inner_instructions,
                    account_keys,
                    wallet_address
                )
                if parsed:
                    operations.append(parsed)

        return operations

    def _parse_jupiter_swap(
        self,
        instr: Dict,
        inner_instructions: List[Dict],
        account_keys: List[str],
        wallet_address: str
    ) -> Optional[Dict]:
        """Parsea un swap de Jupiter"""
        # Jupiter usa TransferChecked en inner instructions
        # Buscar transferencias de token en inner instructions
        return self._find_swap_in_inner_instructions(
            inner_instructions,
            account_keys,
            wallet_address,
            instr.get("parsed", {}).get("info", {}).get("instructionIndex", -1)
        )

    def _parse_raydium_swap(
        self,
        instr: Dict,
        inner_instructions: List[Dict],
        account_keys: List[str],
        wallet_address: str
    ) -> Optional[Dict]:
        """Parsea un swap de Raydium"""
        return self._find_swap_in_inner_instructions(
            inner_instructions,
            account_keys,
            wallet_address,
            instr.get("parsed", {}).get("info", {}).get("instructionIndex", -1)
        )

    def _parse_orca_swap(
        self,
        instr: Dict,
        inner_instructions: List[Dict],
        account_keys: List[str],
        wallet_address: str
    ) -> Optional[Dict]:
        """Parsea un swap de Orca"""
        return self._find_swap_in_inner_instructions(
            inner_instructions,
            account_keys,
            wallet_address,
            instr.get("parsed", {}).get("info", {}).get("instructionIndex", -1)
        )

    def _find_swap_in_inner_instructions(
        self,
        inner_instructions: List[Dict],
        account_keys: List[str],
        wallet_address: str,
        instruction_index: int
    ) -> Optional[Dict]:
        """Busca operaciones de swap en las inner instructions"""
        token_transfers = []

        for inner in inner_instructions:
            if inner.get("index") != instruction_index:
                continue

            for inner_instr in inner.get("instructions", []):
                parsed = inner_instr.get("parsed", {})
                instr_type = parsed.get("type", "")
                info = parsed.get("info", {})

                # Buscar TransferChecked o Transfer
                if instr_type in ["transferChecked", "transfer"]:
                    authority = info.get("authority") or info.get("owner")
                    source = info.get("source") or info.get("account")
                    destination = info.get("destination")
                    mint = info.get("mint")

                    if authority and (authority == wallet_address or source == wallet_address or destination == wallet_address):
                        amount_str = info.get("tokenAmount", {})
                        if isinstance(amount_str, dict):
                            amount = float(amount_str.get("amount", 0))
                            decimals = amount_str.get("decimals", 6)
                        else:
                            amount = float(amount_str) if amount_str else 0
                            decimals = 6

                        token_amount = amount / (10 ** decimals) if decimals > 0 else amount

                        # Determinar dirección
                        if source == wallet_address or (authority == wallet_address and destination != wallet_address):
                            direction = "out"
                        else:
                            direction = "in"

                        token_transfers.append({
                            "mint": mint,
                            "amount": token_amount,
                            "direction": direction
                        })

        # Si tenemos 2 transferencias (una out, una in), es un swap
        if len(token_transfers) >= 2:
            out_tokens = [t for t in token_transfers if t["direction"] == "out"]
            in_tokens = [t for t in token_transfers if t["direction"] == "in"]

            if out_tokens and in_tokens:
                # Determinar qué token es el input y cuál el output
                from_token = out_tokens[0]
                to_token = in_tokens[0]

                # El input es lo que vendiste (SELL), el output es lo que compraste (BUY)
                # Pero necesitamos determinar si es SOL -> Token (BUY) o Token -> SOL (SELL)
                from_is_sol = from_token["mint"] == "So11111111111111111111111111111111111111112"

                if from_is_sol:
                    # SOL -> Token = COMPRA
                    return {
                        "type": TransactionType.BUY,
                        "token_address": to_token["mint"],
                        "token_symbol": self._get_token_info(to_token["mint"])[0],
                        "token_amount": to_token["amount"],
                        "sol_amount": from_token["amount"],
                        "price_per_token": from_token["amount"] / to_token["amount"] if to_token["amount"] > 0 else 0
                    }
                else:
                    # Token -> SOL = VENTA
                    return {
                        "type": TransactionType.SELL,
                        "token_address": from_token["mint"],
                        "token_symbol": self._get_token_info(from_token["mint"])[0],
                        "token_amount": from_token["amount"],
                        "sol_amount": to_token["amount"],
                        "price_per_token": to_token["amount"] / from_token["amount"] if from_token["amount"] > 0 else 0
                    }

        return None

    def _parse_token_transfers(
        self,
        pre_balances: List[Dict],
        post_balances: List[Dict],
        wallet_address: str,
        account_keys: List[str]
    ) -> List[Dict]:
        """Parsea transferencias de token"""
        transfers = []

        # Crear mapa de pre-balances
        pre_balance_map = {}
        for balance in pre_balances:
            account_index = balance.get("accountIndex")
            mint = balance.get("mint", "")
            amount = float(balance.get("uiTokenAmount", {}).get("amount", 0))
            pre_balance_map[account_index] = (mint, amount)

        # Buscar cambios en post-balances
        for balance in post_balances:
            account_index = balance.get("accountIndex")
            mint = balance.get("mint", "")
            ui_amount = balance.get("uiTokenAmount", {})
            amount = float(ui_amount.get("amount", 0))

            # Obtener pre-balance
            pre_info = pre_balance_map.get(account_index)
            if not pre_info:
                continue

            pre_mint, pre_amount = pre_info
            if pre_mint != mint:
                continue

            change = amount - pre_amount

            if abs(change) > 0.000001:
                # Verificar si esta cuenta pertenece a la wallet
                owner = balance.get("owner", "")
                if owner == wallet_address:
                    symbol, decimals = self._get_token_info(mint)
                    transfers.append({
                        "type": TransactionType.TRANSFER_IN if change > 0 else TransactionType.TRANSFER_OUT,
                        "mint": mint,
                        "symbol": symbol,
                        "amount": abs(change),
                        "decimals": decimals
                    })

        return transfers

    def _get_token_info(self, mint: str) -> Tuple[str, int]:
        """Obtiene información de un token (símbolo, decimales)"""
        if mint in KNOWN_TOKENS:
            return KNOWN_TOKENS[mint]
        return ("TOKEN", 6)
