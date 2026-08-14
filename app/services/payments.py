"""Verificación de pagos en SOL para CE BetterTrader PRO.

Cuando un usuario no está en la whitelist, puede pagar ACCESS_PRICE_SOL a
MERCHANT_WALLET. Este servicio consulta la API de Helius por las
transacciones entrantes de la wallet de comercio y confirma que llegó una
transferencia nativa de SOL del pagador con el importe requerido.

Una vez confirmada, AuthService.record_payment() otorga acceso durante
ACCESS_DURATION_DAYS.
"""
import time
from typing import Optional

from app.config import get_settings
from app.services.helius_parser import HeliusAPI

settings = get_settings()


def _lamports_to_sol(lamports: int) -> float:
    return lamports / 1e9


async def verify_payment(wallet_address: str, amount_sol: float) -> Optional[dict]:
    """Comprueba si `wallet_address` ya envió >= amount_sol a MERCHANT_WALLET.

    Devuelve {tx_signature, amount_sol} si hay un pago confirmado, o None.
    Usa la API REST de Helius (transacciones parseadas con nativeTransfers).
    """
    merchant = settings.MERCHANT_WALLET
    if not merchant:
        return None

    api = HeliusAPI()
    try:
        txs = await api.get_transactions(merchant, limit=50)
    except Exception as e:
        print(f"Error verificando pagos: {e}")
        return None
    finally:
        await api.close()

    required = amount_sol * 1e9  # en lamports
    now = time.time()

    for tx in txs:
        ts = tx.get("timestamp")
        if ts and now - ts > 3600 * 24 * 2:  # solo últimos 2 días
            continue
        # nativeTransfers: [{fromUserAccount, toUserAccount, amount}]
        for transfer in tx.get("nativeTransfers", []) or []:
            if (
                transfer.get("fromUserAccount") == wallet_address
                and transfer.get("toUserAccount") == merchant
                and transfer.get("amount", 0) >= required
            ):
                return {
                    "tx_signature": tx.get("signature"),
                    "amount_sol": _lamports_to_sol(transfer["amount"]),
                    "confirmed_at": ts,
                }
    return None
