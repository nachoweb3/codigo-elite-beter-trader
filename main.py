from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pathlib import Path
from app.config import get_settings
from app.api.endpoints import router as api_router
from app.api.trading import router as trading_router
from app.api.auth_api import router as auth_router
from app.services.auth import auth_service

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("CE BetterTrader API iniciando...")
    print(f"RPC URL: {settings.helius_rpc_url}")
    print(f"API running on http://{settings.API_HOST}:{settings.API_PORT}")
    yield
    print("CE BetterTrader API deteniéndose...")


app = FastAPI(
    title="CE BetterTrader API",
    description="Solana Memecoin Trading Analyzer & Auto-Trading",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS
origins = settings.CORS_ORIGINS.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Evita que el navegador sirva HTML/CSS/JS cacheados.

    El problema real que teníamos: el navegador cacheaba index.html y
    style.css viejos, así que los usuarios seguían viendo la versión
    anterior del dashboard (sin tasa de éxito, sin perfil, etc.) aunque
    el servidor ya tuviera el código nuevo.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


class AccessControlMiddleware(BaseHTTPMiddleware):
    """Protege la API detrás de una sesión de wallet.

    Cuando ACCESS_CONTROL=True (producción), todas las rutas /api/* exigen
    un token de sesión válido (Authorization: Bearer <token>), excepto:
    - /api/auth/* (login, challenge, pay/check, admin) — necesarias para
      poder autenticarse
    - /api/health

    El frontend guarda el token en localStorage y lo adjunta a cada fetch.
    Sin sesión válida -> 401; la app muestra la pantalla de login/pago.
    """

    PUBLIC_PREFIXES = ("/api/auth/", "/api/health")

    async def dispatch(self, request, call_next):
        path = request.url.path

        if settings.ACCESS_CONTROL and path.startswith("/api/") and not any(
            path.startswith(p) for p in self.PUBLIC_PREFIXES
        ):
            auth_header = request.headers.get("authorization", "")
            token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            sess = auth_service.get_session(token)
            if not sess:
                return JSONResponse(
                    {"detail": "Acceso restringido: inicia sesión con tu wallet"},
                    status_code=401,
                )
            request.state.session = sess

        return await call_next(request)


app.add_middleware(NoCacheMiddleware)
app.add_middleware(AccessControlMiddleware)

# API Routes
app.include_router(api_router)
app.include_router(trading_router)
app.include_router(auth_router)

# Serve static files
static_dir = Path(__file__).parent / "app" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    print(f"WARNING: Static directory not found at {static_dir}")


@app.get("/")
async def root():
    """Serve the main page"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "CE BetterTrader API - Analizador de Trading en Solana", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True
    )
