from typing import List, Optional, Dict, Any
from datetime import datetime
from app.models.schemas import Transaction, TransactionType, TokenType
from app.config import get_settings

settings = get_settings()


class TransactionParser:
    """Parsea transacciones de Solana y las clasifica"""

    # DEX Program IDs
    DEX_PROGRAMS = {
        settings.RAYDIUM_PROGRAM: "Raydium",
        settings.JUPITER_PROGRAM: "Jupiter",
        settings.ORCA_PROGRAM: "Orca",
    }

    # Tokens estables conocidos
    STABLECOINS = {
        settings.USDC_MINT: "USDC",
        settings.USDT_MINT: "USDT",
        "So11111111111111111111111111111111111111112": "SOL",
    }

    @staticmethod
    def parse_transaction(tx_data: Dict, wallet_address: str) -> List[Transaction]:
        """Parsea una transacción y extrae las operaciones relevantes"""
        if not tx_data:
            return []

        transactions = []
        meta = tx_data.get("meta", {})
        transaction = tx_data.get("transaction", {}).get("message", {})

        if not meta:
            return []

        # Obtener timestamp
        block_time = tx_data.get("blockTime")
        timestamp = datetime.fromtimestamp(block_time) if block_time else datetime.now()

        # Obtener fee
        fee = meta.get("fee", 0) / 1e9  # Convertir lamports a SOL

        # Obtener balances antes y después
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        account_keys = transaction.get("accountKeys", [])

        # Buscar índice de la wallet
        wallet_index = None
        for i, key in enumerate(account_keys):
            if isinstance(key, str) and key == wallet_address:
                wallet_index = i
                break

        if wallet_index is None:
            return []

        # Calcular cambio de balance
        sol_change = (post_balances[wallet_index] - pre_balances[wallet_index]) / 1e9

        # Parsear instrucciones
        instructions = transaction.get("instructions", [])
        inner_instructions = meta.get("innerInstructions", [])

        for idx, instr in enumerate(instructions):
            program_id = instr.get("programId")
            program_name = TransactionParser.DEX_PROGRAMS.get(program_id, "Unknown")

            if program_name != "Unknown":
                # Es una transacción de DEX
                parsed = TransactionParser._parse_dex_instruction(
                    instr, inner_instructions, wallet_address,
                    timestamp, tx_data.get("slot"), fee
                )
                if parsed:
                    transactions.extend(parsed)

        # Si no encontramos transacciones de DEX pero hubo cambio de SOL
        if not transactions and abs(sol_change) > 0.000001:
            transactions.append(Transaction(
                signature=tx_data.get("transaction", {}).get("signatures", [""])[0],
                timestamp=timestamp,
                type=TransactionType.TRANSFER_IN if sol_change > 0 else TransactionType.TRANSFER_OUT,
                token_address=settings.WSOL_MINT,
                token_symbol="SOL",
                token_amount=abs(sol_change),
                sol_amount=abs(sol_change),
                price_per_token=1.0,
                fee=fee,
                slot=tx_data.get("slot")
            ))

        return transactions

    @staticmethod
    def _parse_dex_instruction(
        instr: Dict,
        inner_instructions: List[Dict],
        wallet_address: str,
        timestamp: datetime,
        slot: Optional[int],
        fee: float
    ) -> List[Transaction]:
        """Parsea una instrucción de DEX"""
        transactions = []
        program_id = instr.get("programId")
        accounts = instr.get("accounts", [])
        data = instr.get("data", [])

        # Buscar transferencias de token en las inner instructions
        for inner in inner_instructions:
            if inner.get("index") != instr.get("parsed", {}).get("info", {}).get("instructionIndex", -1):
                continue

            for inner_instr in inner.get("instructions", []):
                parsed = inner_instr.get("parsed", {})
                instr_type = parsed.get("type", "")
                info = parsed.get("info", {})

                # Transferencias de token
                if instr_type == "transfer" or instr_type == "transferChecked":
                    source = info.get("source") or info.get("authority")
                    destination = info.get("destination")
                    amount = info.get("amount") or info.get("tokenAmount", {}).get("amount", "0")

                    # Determinar dirección basada en las cuentas
                    token_address = info.get("mint", "")
                    token_amount = float(amount) / 1e6 if "." not in str(amount) else float(amount)

                    # Simplificado: asumimos swap si hay source y destination
                    if source and destination:
                        transactions.append(Transaction(
                            signature="",  # Se asignará después
                            timestamp=timestamp,
                            type=TransactionType.SWAP,
                            token_address=token_address,
                            token_symbol=TransactionParser._get_token_symbol(token_address),
                            token_amount=token_amount,
                            sol_amount=0,  # Se calculará en el análisis
                            price_per_token=0,
                            fee=fee,
                            slot=slot
                        ))

        return transactions

    @staticmethod
    def _get_token_symbol(mint_address: str) -> Optional[str]:
        """Obtiene el símbolo de un token (placeholder)"""
        return TransactionParser.STABLECOINS.get(mint_address, "UNKNOWN")

    @staticmethod
    def extract_swap_info(
        pre_balances: Dict[str, float],
        post_balances: Dict[str, float],
        wallet_address: str
    ) -> Dict[str, Any]:
        """Extrae información de un swap comparando balances antes y después"""
        swaps = []

        for mint, post_amount in post_balances.items():
            pre_amount = pre_balances.get(mint, 0)
            change = post_amount - pre_amount

            if abs(change) > 0.000001:
                swaps.append({
                    "mint": mint,
                    "change": change,
                    "is_inflow": change > 0
                })

        return {"swaps": swaps}


async def parse_signatures(
    signatures: List[Dict],
    rpc_client
) -> List[Transaction]:
    """Parsea una lista de firmas y retorna las transacciones"""
    transactions = []

    for sig in signatures:
        signature = sig.get("signature")
        if not signature:
            continue

        # Verificar si está en caché
        cached = get_cached_transaction(signature)
        if cached:
            continue

        # Obtener transacción
        tx_data = await rpc_client.get_transaction(signature)
        if not tx_data:
            continue

        # Cache
        cache_transaction(signature, tx_data)

    return transactions
