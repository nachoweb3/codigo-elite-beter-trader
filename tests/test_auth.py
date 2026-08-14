"""Tests del sistema de acceso: firma de Solana, whitelist, pagos, sesiones."""
import time
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.signature import Signature

from app.services.auth import AuthService


@pytest.fixture(autouse=True)
def _fresh_auth(monkeypatch, tmp_path):
    """Aísla el servicio con datos temporales."""
    from app.services import auth as auth_module

    monkeypatch.setattr(auth_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_module, "WHITELIST_FILE", tmp_path / "whitelist.json")
    monkeypatch.setattr(auth_module, "PAYMENTS_FILE", tmp_path / "payments.json")

    service = AuthService()
    monkeypatch.setattr(auth_module, "auth_service", service)
    return service


class TestSignatureLogin:
    def test_valid_signature_creates_session(self, _fresh_auth):
        service = _fresh_auth
        kp = Keypair()
        wallet = str(kp.pubkey())

        challenge = service.create_challenge(wallet)
        sig = kp.sign_message(challenge.encode("utf-8"))
        sig_b58 = __import__("base58").b58encode(bytes(sig)).decode()

        assert service.verify_signature(wallet, challenge, sig_b58) is True
        token = service.create_session(wallet)
        sess = service.get_session(token)
        assert sess is not None
        assert sess["wallet"] == wallet

    def test_wrong_message_rejected(self, _fresh_auth):
        service = _fresh_auth
        kp = Keypair()
        wallet = str(kp.pubkey())

        challenge = service.create_challenge(wallet)
        sig = kp.sign_message(b"otro mensaje")
        sig_b58 = __import__("base58").b58encode(bytes(sig)).decode()

        assert service.verify_signature(wallet, challenge, sig_b58) is False

    def test_expired_challenge_rejected(self, _fresh_auth):
        service = _fresh_auth
        kp = Keypair()
        wallet = str(kp.pubkey())

        challenge = service.create_challenge(wallet)
        service._challenges[wallet]["expires"] = time.time() - 10
        sig = kp.sign_message(challenge.encode("utf-8"))
        sig_b58 = __import__("base58").b58encode(bytes(sig)).decode()

        assert service.verify_signature(wallet, challenge, sig_b58) is False

    def test_unknown_challenge_rejected(self, _fresh_auth):
        service = _fresh_auth
        assert service.verify_signature("fake", "challenge", "sig") is False


class TestAccess:
    def test_whitelist_grants_access(self, _fresh_auth):
        service = _fresh_auth
        wallet = "WalletWhitelist1"
        assert service.has_access(wallet) is False
        service.grant_whitelist(wallet)
        assert service.has_access(wallet) is True
        assert service.access_status(wallet)["whitelisted"] is True
        service.revoke_whitelist(wallet)
        assert service.has_access(wallet) is False

    def test_payment_grants_timed_access(self, _fresh_auth):
        service = _fresh_auth
        wallet = "WalletPaga"
        assert service.has_access(wallet) is False

        service.record_payment(wallet, 0.1, "tx123")
        assert service.has_access(wallet) is True
        status = service.access_status(wallet)
        assert status["paid"] is True
        assert status["remaining_days"] > 0
        assert status["expires"] is not None

    def test_expired_payment_denies_access(self, _fresh_auth):
        service = _fresh_auth
        wallet = "WalletExpirada"
        service.record_payment(wallet, 0.1, "tx1")
        service.payments["payments"][wallet]["expires"] = time.time() - 10
        assert service.has_access(wallet) is False

    def test_payment_renews_from_today(self, _fresh_auth):
        service = _fresh_auth
        wallet = "WalletRenueva"
        service.record_payment(wallet, 0.1, "tx1")
        first = service.payments["payments"][wallet]["expires"]
        service.record_payment(wallet, 0.1, "tx2")
        second = service.payments["payments"][wallet]["expires"]
        # Renueva (extiende), no acumula desde la fecha anterior
        assert second > first

    def test_sessions_expire(self, _fresh_auth):
        service = _fresh_auth
        token = service.create_session("wallet1")
        service.sessions[token]["expires"] = time.time() - 1
        assert service.get_session(token) is None
