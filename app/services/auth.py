"""Acceso y monetización de CE BetterTrader PRO.

Sistema de acceso por wallet de Solana:
- **Login con firma**: el usuario firma un challenge con su wallet
  (ed25519, verificado en el servidor). Nadie comparte private keys.
- **Whitelist**: el administrador autoriza wallets directamente (acceso
  indefinido). Ideal para los miembros de tu comunidad privada.
- **Pago en SOL**: quien no está en la whitelist puede pagar X SOL a la
  wallet de comercio (MERCHANT_WALLET). El servidor verifica la
  transferencia entrante en la blockchain y concede acceso temporal.
- **Sesiones**: token firmado en memoria, con expiración.

Los datos (whitelist y pagos) se persisten en data/ con escritura atómica.
"""
import json
import secrets
import time
import base58
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings
from app.services.storage import atomic_write_json
from app.services.supabase import supabase_store

settings = get_settings()

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WHITELIST_FILE = DATA_DIR / "whitelist.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"

SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 horas


def _load_json(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


class AuthService:
    """Gestiona login por firma de Solana, whitelist, pagos y sesiones."""

    def __init__(self):
        self.whitelist = _load_json(WHITELIST_FILE, {"wallets": {}})
        self.payments = _load_json(PAYMENTS_FILE, {"payments": {}})
        if supabase_store.enabled:
            self.whitelist = supabase_store.load("auth_whitelist", self.whitelist)
            self.payments = supabase_store.load("auth_payments", self.payments)
        # Sesiones en memoria: token -> {"wallet", "expires"}
        self.sessions: Dict[str, dict] = {}
        self._challenges: Dict[str, dict] = {}

    # ----------------------------------------------------------
    # Challenge / verificación de firma
    # ----------------------------------------------------------
    def create_challenge(self, wallet_address: str) -> str:
        """Genera un mensaje corto y con caducidad para firmar con la wallet."""
        challenge = (
            "CE BetterTrader PRO - Inicio de sesión\n"
            f"Wallet: {wallet_address}\n"
            f"Nonce: {secrets.token_hex(8)}\n"
            f"Expira: {int(time.time()) + 300}"
        )
        self._challenges[wallet_address] = {"challenge": challenge, "expires": time.time() + 300}
        return challenge

    def verify_signature(self, wallet_address: str, challenge: str, signature: str) -> bool:
        """Verifica una firma ed25519 del challenge con la clave pública de la wallet."""
        try:
            rec = self._challenges.get(wallet_address)
            if not rec or rec["challenge"] != challenge or rec["expires"] < time.time():
                return False

            # Verificación ed25519 con la clave pública (nunca tocamos
            # private keys): Signature.verify(pubkey, message) es la misma
            # matemática que usa Solana para validar firmas.
            from solders.signature import Signature
            from solders.pubkey import Pubkey
            pubkey = Pubkey.from_string(wallet_address)
            sig = Signature.from_string(signature)
            return sig.verify(pubkey, challenge.encode("utf-8"))
        except Exception:
            return False

    # ----------------------------------------------------------
    # Sesiones
    # ----------------------------------------------------------
    def create_session(self, wallet_address: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {
            "wallet": wallet_address,
            "expires": time.time() + SESSION_TTL_SECONDS,
        }
        return token

    def get_session(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        sess = self.sessions.get(token)
        if not sess:
            return None
        if sess["expires"] < time.time():
            del self.sessions[token]
            return None
        return sess

    def logout(self, token: str):
        self.sessions.pop(token, None)

    # ----------------------------------------------------------
    # Acceso: whitelist + pagos
    # ----------------------------------------------------------
    def is_admin(self, wallet_address: str) -> bool:
        admins = [w.strip() for w in settings.ADMIN_WALLETS.split(",") if w.strip()]
        return wallet_address in admins

    def is_whitelisted(self, wallet_address: str) -> bool:
        return wallet_address in self.whitelist.get("wallets", {})

    def grant_whitelist(self, wallet_address: str, by_admin: str = ""):
        self.whitelist.setdefault("wallets", {})[wallet_address] = {
            "added_at": datetime.now(timezone.utc).isoformat(),
            "by": by_admin,
        }
        atomic_write_json(WHITELIST_FILE, self.whitelist)
        supabase_store.save("auth_whitelist", self.whitelist)

    def revoke_whitelist(self, wallet_address: str):
        self.whitelist.get("wallets", {}).pop(wallet_address, None)
        atomic_write_json(WHITELIST_FILE, self.whitelist)
        supabase_store.save("auth_whitelist", self.whitelist)

    def get_payment(self, wallet_address: str) -> Optional[dict]:
        return self.payments.get("payments", {}).get(wallet_address)

    def record_payment(self, wallet_address: str, amount_sol: float, tx_signature: str):
        """Registra un pago confirmado y otorga acceso durante ACCESS_DURATION_DAYS."""
        duration = settings.ACCESS_DURATION_DAYS * 86400
        now = time.time()
        existing = self.get_payment(wallet_address) or {}
        # Renueva desde hoy (no se acumulan licencias: cada pago renueva)
        self.payments.setdefault("payments", {})[wallet_address] = {
            "amount_sol": existing.get("amount_sol", 0) + amount_sol,
            "last_tx": tx_signature,
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "expires": now + duration,
        }
        atomic_write_json(PAYMENTS_FILE, self.payments)
        supabase_store.save("auth_payments", self.payments)

    def has_access(self, wallet_address: str) -> bool:
        """¿Tiene acceso? Whitelist (indefinido), admin, demo o pago vigente."""
        if self.is_admin(wallet_address):
            return True
        # La wallet demo siempre entra en modo demo (solo desarrollo)
        if settings.DEMO_MODE and settings.DEMO_WALLET and wallet_address == settings.DEMO_WALLET:
            return True
        if self.is_whitelisted(wallet_address):
            return True
        payment = self.get_payment(wallet_address)
        if payment and payment.get("expires", 0) > time.time():
            return True
        return False

    def access_status(self, wallet_address: str) -> dict:
        """Estado legible de acceso para la UI."""
        payment = self.get_payment(wallet_address)
        expires = payment.get("expires") if payment else None
        remaining_days = None
        if expires:
            remaining_days = max(0, int((expires - time.time()) / 86400))
        return {
            "has_access": self.has_access(wallet_address),
            "is_admin": self.is_admin(wallet_address),
            "whitelisted": self.is_whitelisted(wallet_address),
            "paid": bool(payment),
            "expires": expires,
            "remaining_days": remaining_days,
            "price_sol": settings.ACCESS_PRICE_SOL,
            "merchant_wallet": settings.MERCHANT_WALLET,
            "duration_days": settings.ACCESS_DURATION_DAYS,
        }


auth_service = AuthService()
