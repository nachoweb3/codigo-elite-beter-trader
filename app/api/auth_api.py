"""Endpoints de acceso, login por firma y pagos de CE BetterTrader PRO."""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional

from app.config import get_settings
from app.services.auth import auth_service
from app.services.payments import verify_payment

router = APIRouter(prefix="/api/auth", tags=["Auth"])
settings = get_settings()


def _extract_token(authorization: Optional[str] = "", token: str = "") -> str:
    """Token de sesión: header Authorization: Bearer <token> o query ?token=."""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return token


class ChallengeRequest(BaseModel):
    wallet_address: str = Field(..., description="Solana wallet address")


class VerifyRequest(BaseModel):
    wallet_address: str
    challenge: str
    signature: str


class WhitelistRequest(BaseModel):
    wallet_address: str
    action: str = Field(..., description="'add' o 'remove'")


@router.get("/config")
async def auth_config():
    """Configuración pública de acceso: permite al frontend decidir si
    mostrar la puerta de login (ACCESS_CONTROL) o entrar directo (dev).
    Nunca expone secretos, solo flags y precios."""
    return {
        "access_control": settings.ACCESS_CONTROL,
        "demo_mode": settings.DEMO_MODE,
        "demo_wallet": settings.DEMO_WALLET,
        "price_sol": settings.ACCESS_PRICE_SOL,
        "duration_days": settings.ACCESS_DURATION_DAYS,
        "merchant_wallet": settings.MERCHANT_WALLET,
    }


@router.post("/challenge")
async def get_challenge(req: ChallengeRequest):
    """Paso 1 del login: devuelve el mensaje a firmar con la wallet."""
    if not req.wallet_address:
        raise HTTPException(status_code=400, detail="wallet_address requerida")
    challenge = auth_service.create_challenge(req.wallet_address)
    return {"challenge": challenge, "expires_in": 300}


@router.post("/verify")
async def verify_login(req: VerifyRequest):
    """Paso 2: verifica la firma ed25519 y crea la sesión.

    El frontend firma el challenge con Phantom/Solflare (sin exponer la
    private key) y envía la firma en base58. Si es válida, devolvemos un
    token de sesión + el estado de acceso de la wallet.
    """
    ok = auth_service.verify_signature(req.wallet_address, req.challenge, req.signature)
    if not ok:
        raise HTTPException(status_code=401, detail="Firma inválida o challenge expirado")

    token = auth_service.create_session(req.wallet_address)
    return {
        "token": token,
        "wallet": req.wallet_address,
        "access": auth_service.access_status(req.wallet_address),
    }


@router.get("/me")
async def session_info(authorization: str = Header(""), token: str = ""):
    """Estado de la sesión actual + acceso de la wallet."""
    sess = auth_service.get_session(_extract_token(authorization, token))
    if not sess:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    return {
        "wallet": sess["wallet"],
        "access": auth_service.access_status(sess["wallet"]),
    }


@router.post("/demo")
async def demo_login():
    """Sesión de desarrollo: entra con DEMO_WALLET sin firma real.
    Solo activa si DEMO_MODE=True en el .env (nunca en producción)."""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=403, detail="Modo demo desactivado")
    wallet = settings.DEMO_WALLET or "demo-wallet"
    token = auth_service.create_session(wallet)
    return {
        "token": token,
        "wallet": wallet,
        "access": auth_service.access_status(wallet),
    }


@router.post("/logout")
async def logout(authorization: str = Header(""), token: str = ""):
    auth_service.logout(_extract_token(authorization, token))
    return {"ok": True}


@router.get("/access")
async def access_status(wallet_address: str = ""):
    """Estado de acceso público (sin sesión): para mostrar precios y lock."""
    if not wallet_address:
        raise HTTPException(status_code=400, detail="wallet_address requerida")
    status = auth_service.access_status(wallet_address)
    return status


@router.post("/pay/check")
async def check_payment(authorization: str = Header(""), token: str = ""):
    """Comprueba si la wallet de la sesión ya pagó y activa el acceso.

    Consulta la blockchain (transacciones entrantes a MERCHANT_WALLET).
    Si encuentra el pago, registra la licencia y devuelve acceso vigente.
    """
    sess = auth_service.get_session(_extract_token(authorization, token))
    if not sess:
        raise HTTPException(status_code=401, detail="Sesión no válida")

    wallet = sess["wallet"]
    if auth_service.has_access(wallet):
        return {"paid": True, "access": auth_service.access_status(wallet)}

    if not settings.MERCHANT_WALLET:
        raise HTTPException(
            status_code=400,
            detail="MERCHANT_WALLET no configurada: el administrador debe configurar la wallet de pagos en el .env",
        )

    payment = await verify_payment(wallet, settings.ACCESS_PRICE_SOL)
    if payment:
        auth_service.record_payment(
            wallet,
            payment["amount_sol"],
            payment["tx_signature"],
        )
        return {
            "paid": True,
            "tx_signature": payment["tx_signature"],
            "access": auth_service.access_status(wallet),
        }

    return {"paid": False, "access": auth_service.access_status(wallet)}


@router.post("/admin/whitelist")
async def admin_whitelist(req: WhitelistRequest, authorization: str = Header(""), token: str = ""):
    """Solo administradores: añadir/quitar wallets de la whitelist."""
    sess = auth_service.get_session(_extract_token(authorization, token))
    if not sess:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    if not auth_service.is_admin(sess["wallet"]):
        raise HTTPException(status_code=403, detail="Solo administradores")

    if req.action == "add":
        auth_service.grant_whitelist(req.wallet_address, by_admin=sess["wallet"])
    elif req.action == "remove":
        auth_service.revoke_whitelist(req.wallet_address)
    else:
        raise HTTPException(status_code=400, detail="action debe ser 'add' o 'remove'")

    return {"ok": True, "wallet": req.wallet_address, "action": req.action}


@router.get("/admin/wallets")
async def admin_wallets(authorization: str = Header(""), token: str = ""):
    """Solo administradores: lista de wallets con acceso (whitelist + pagos)."""
    sess = auth_service.get_session(_extract_token(authorization, token))
    if not sess:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    if not auth_service.is_admin(sess["wallet"]):
        raise HTTPException(status_code=403, detail="Solo administradores")

    wallets = []
    for w in auth_service.whitelist.get("wallets", {}):
        wallets.append({"wallet": w, "type": "whitelist", "status": auth_service.access_status(w)})
    for w, p in auth_service.payments.get("payments", {}).items():
        wallets.append({"wallet": w, "type": "pago", "status": auth_service.access_status(w)})

    return {"wallets": wallets, "total": len(wallets)}
