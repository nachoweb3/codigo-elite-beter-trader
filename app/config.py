from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Solana RPC ---
    # RPC público (gratuito, con límites de rate). Para producción usa
    # Helius, QuickNode o Alchemy y configura HELIUS_API_KEY.
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_WS_URL: str = "wss://api.mainnet-beta.solana.com"

    # --- Helius (opcional) ---
    # Mejora el parseo de swaps, metadatos de tokens y balances.
    # Consíguela gratis en https://dashboard.helius.dev
    HELIUS_API_KEY: str = ""

    # --- API ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:8000,http://localhost:3000"

    # --- Cache ---
    CACHE_TTL: int = 300  # seconds

    # --- Acceso y monetización ---
    # Control de acceso: True en producción. Cuando está desactivado, la app
    # funciona abierta (útil para desarrollo local).
    ACCESS_CONTROL: bool = False
    # Wallets administradoras (separadas por coma): acceso total + pueden
    # añadir/eliminar wallets de la whitelist.
    ADMIN_WALLETS: str = ""
    # Wallet que recibe los pagos en SOL (tu wallet de comercio).
    MERCHANT_WALLET: str = ""
    # Precio del acceso y duración de la licencia.
    ACCESS_PRICE_SOL: float = 0.1
    ACCESS_DURATION_DAYS: int = 30
    # Modo demo: permite crear una sesión con DEMO_WALLET sin firma real
    # (solo para desarrollo; desactívalo en producción).
    DEMO_MODE: bool = False
    DEMO_WALLET: str = ""
    # Nunca aceptar private keys desde una petición web en producción.
    # El flujo seguro debe firmar localmente con Phantom/Solflare.
    ALLOW_SERVER_SIDE_TRADING: bool = False

    # Known DEXs
    RAYDIUM_PROGRAM: str = "675kPX9MHTjS2zt1qf1WNgJuw1MWgCPLHY4vcvwHbZZE"
    JUPITER_PROGRAM: str = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
    ORCA_PROGRAM: str = "9W959DqEETiGZocYGoQkVjyJk8C8UAGkAzNfyiFw3zWg"

    # Known Token Mints
    WSOL_MINT: str = "So11111111111111111111111111111111111111112"
    USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    USDT_MINT: str = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

    model_config = {"env_file": ".env", "case_sensitive": True}

    @property
    def helius_api_key(self) -> str:
        """API key de Helius: desde la variable dedicada o derivada de la URL RPC."""
        if self.HELIUS_API_KEY:
            return self.HELIUS_API_KEY
        # Compatibilidad hacia atrás: si el RPC usa api-key, reutilizarla
        if "api-key=" in self.SOLANA_RPC_URL:
            return self.SOLANA_RPC_URL.split("api-key=")[-1].split("&")[0]
        return ""

    @property
    def helius_rpc_url(self) -> str:
        """URL RPC de Helius si hay API key configurada."""
        if self.HELIUS_API_KEY:
            return f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        return self.SOLANA_RPC_URL

    @property
    def helius_ws_url(self) -> str:
        """URL WebSocket de Helius si hay API key configurada."""
        if self.HELIUS_API_KEY:
            return f"wss://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        return self.SOLANA_WS_URL

    @property
    def helius_api_url(self) -> str:
        """Base URL de la API REST de Helius."""
        return "https://api.helius.xyz"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
