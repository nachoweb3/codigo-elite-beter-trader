# CE BetterTrader - Analizador de Trading en Solana

App que analiza tus trades de memecoins en Solana y te ayuda a mejorar tus decisiones de trading mediante análisis de P&L, métricas avanzadas y recomendaciones personalizadas por wallet.

## Características

- **Análisis de P&L**: Calcula tu profit/loss total, por token y por trade
- **Win Rate**: Mide tu porcentaje de trades ganadores
- **Profit Factor**: Ratio entre ganancias brutas y pérdidas brutas
- **Métricas Avanzadas**: Hold time promedio, Sharpe ratio, tamaño de trades
- **Patrones de Trading**: Detecta patrones en tu estilo de trading
- **Recomendaciones**: Sugerencias personalizadas para mejorar
- **Dashboard Visual**: Gráficos de P&L por token y distribución de trades
- **Análisis de Flujo de Dinero**: A dónde va tu dinero y por qué (acumulación/distribución)
- **Holdings en tiempo real**: Tokens actuales de la wallet con valor en USD
- **Auto-Trading PRO**: Quotes y ejecución de swaps vía Jupiter, estrategias DCA/Grid/Signal

## Instalación

1. **Crear entorno virtual**:
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

2. **Instalar dependencias**:
```bash
pip install -r requirements.txt
# Para desarrollo/tests:
pip install -r requirements-dev.txt
```

3. **Configurar variables de entorno**:
```bash
cp .env.example .env
# Editar .env con tus preferencias
```

> 🔑 **Helius API Key (opcional)**: consigue una key gratis en [dashboard.helius.dev](https://dashboard.helius.dev) y ponla en `HELIUS_API_KEY`. **No es necesaria para los metadatos/logos de tokens**: esos se leen on-chain (programa Token Metadata de Solana) vía el RPC público. La key solo mejora el parseo de swaps y elimina límites de rate. Sin key, la app usa el RPC público de Solana.

## Uso

**Iniciar el servidor**:
```bash
python main.py
```

O con uvicorn directamente:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Abrir el navegador**:
- Dashboard: http://localhost:8000
- API Docs: http://localhost:8000/docs

**Ejecutar los tests**:
```bash
python -m pytest tests/ -v
```

## Endpoints de la API

| Endpoint | Descripción |
|----------|-------------|
| `GET /` | Dashboard web |
| `GET /api/health` | Health check público para Render |
| `GET /api/wallet/balance?wallet=...` | Balance de SOL |
| `GET /api/wallet/transactions?wallet=...&limit=50` | Transacciones |
| `POST /api/wallet/analyze` | Análisis completo |
| `GET /api/wallet/flow-directions?wallet=...` | Flujo de dinero por token |
| `GET /api/wallet/portfolio?wallet=...` | Holdings actuales |
| `POST /api/wallet/simulate-stream` | Stream de actividad |
| `GET /api/market/sol-price` | Precio de SOL (con caché y fallback) |
| `POST /api/trading/quote` | Quote de swap vía Jupiter |
| `POST /api/trading/execute` | Ejecutar swap (desactivado por defecto por seguridad) |

## Configuración

Variables de entorno (ver `.env.example`):

| Variable | Descripción | Default |
|----------|-------------|---------|
| `SOLANA_RPC_URL` | URL RPC de Solana | RPC público |
| `HELIUS_API_KEY` | Key de Helius (recomendada) | vacío |
| `API_HOST` / `API_PORT` | Bind del servidor | `0.0.0.0` / `8000` |
| `CORS_ORIGINS` | Orígenes permitidos | localhost:8000/3000 |
| `CACHE_TTL` | TTL de caché en segundos | `300` |
| `ACCESS_CONTROL` | Activa login por wallet | `false` |
| `ADMIN_WALLETS` | Wallets administradoras separadas por comas | vacío |
| `MERCHANT_WALLET` | Wallet que recibe los pagos | vacío |
| `ACCESS_PRICE_SOL` | Precio de acceso | `0.1` |
| `ACCESS_DURATION_DAYS` | Días de licencia | `30` |
| `ALLOW_SERVER_SIDE_TRADING` | Ejecución con private key en servidor | `false` |

## Tecnologías

- **Backend**: FastAPI (Python)
- **Blockchain**: Solana RPC + Helius API (REST/WebSocket)
- **Trading**: Jupiter API (quotes y swaps)
- **Frontend**: HTML + JavaScript vanilla
- **Gráficos**: Chart.js

## Estructura del Proyecto

```
bettertrader/
├── app/
│   ├── api/           # Endpoints de FastAPI
│   ├── blockchain/    # Clientes Solana RPC / Helius / WebSocket
│   ├── models/        # Esquemas Pydantic
│   ├── services/      # Analizador, parser, caché, market data
│   ├── static/        # Frontend
│   └── config.py      # Configuración (env-driven)
├── tests/             # Tests unitarios (pytest)
├── main.py            # Entry point
├── requirements.txt   # Dependencias
└── .env.example       # Variables de entorno
```

## Despliegue gratuito: Hugging Face + Supabase

El `Dockerfile` también es compatible con Hugging Face Spaces (puerto 7860).
Para que whitelist, pagos, comunidad y feedback sobrevivan a los reinicios del
Space gratuito, crea un proyecto en [Supabase](https://supabase.com/dashboard),
ejecuta `supabase_schema.sql` en **SQL Editor** y configura en los Secrets del
backend:

```env
SUPABASE_URL=https://TU_PROYECTO.supabase.co
SUPABASE_SERVICE_ROLE_KEY=TU_CLAVE_SERVICE_ROLE
```

La `service_role` key solo debe existir en el backend; nunca la pongas en el
frontend ni en GitHub. Si las variables no están configuradas, la aplicación
sigue funcionando con JSON local para desarrollo.

### Publicar el Space gratis

1. Crea un Space Docker vacío en [huggingface.co/new-space](https://huggingface.co/new-space), con CPU Basic Free.
2. Añade las variables de Helius, acceso, pagos y Supabase en **Settings > Variables and secrets**.
3. Añade el remoto del Space y publica la rama `main`:

```bash
git remote add huggingface https://huggingface.co/spaces/TU_USUARIO/ce-bettertrader
git fetch huggingface
git push --force-with-lease huggingface main
```

El Space dormirá tras inactividad en el plan gratuito. La URL pública será la
que muestre Hugging Face, normalmente `https://TU_USUARIO-ce-bettertrader.hf.space`.

## Publicar en GitHub y Render

La configuración de producción ya está preparada en `Dockerfile`, `render.yaml` y
`.github/workflows/ci.yml`. El servicio usa un único worker porque las sesiones de
wallet son en memoria, respeta el puerto `PORT` que asigna Render y guarda
`data/whitelist.json` y `data/payments.json` en un disco persistente.

> **Importante:** el plan `starter` de Render es intencional. El disco persistente
> evita perder whitelist y pagos en cada despliegue. El plan gratuito puede dormir y
> no es adecuado para un acceso de pago con datos persistentes.

### 1. Crear el repositorio de GitHub

Desde una terminal situada en la carpeta `bettertrader`:

```bash
git init
git branch -M main
git add .
git status
git commit -m "Preparar CE BetterTrader para Render"
git remote add origin https://github.com/TU_USUARIO/ce-bettertrader.git
git push -u origin main
```

Crea antes en GitHub un repositorio vacío llamado `ce-bettertrader` (sin README ni
`.gitignore` para evitar conflictos). Comprueba `git status` antes de confirmar que
`.env`, `data/`, `.freebuff/` y los logs no aparecen. **Nunca subas una API key,
seed o private key.** Si una clave llegó a estar en Git, revócala y genera otra.

Si el repositorio ya existe, omite `git init`, `git remote add` y usa el remoto
correspondiente. No uses `git add -A` desde una carpeta superior a este proyecto.

### 2. Crear el servicio en Render

1. Entra en [render.com](https://render.com), crea una cuenta y pulsa **New → Blueprint**.
2. Conecta GitHub y selecciona `ce-bettertrader`.
3. Render leerá `render.yaml`; acepta el servicio `ce-bettertrader` y el disco de 1 GB.
4. En las variables marcadas como secretas introduce:
   - `HELIUS_API_KEY`: tu clave de [Helius](https://dashboard.helius.dev).
   - `ADMIN_WALLETS`: tu wallet pública, sin espacios y separada por comas si hay varias.
   - `MERCHANT_WALLET`: wallet pública que recibirá los pagos. Usa una wallet de comercio separada.
5. Verifica que Render mantiene `ACCESS_CONTROL=true`, `DEMO_MODE=false` y
   `ALLOW_SERVER_SIDE_TRADING=false`.
6. Pulsa **Apply** y espera a que el health check `/api/health` quede verde.

La URL será parecida a `https://ce-bettertrader.onrender.com`. La app se sirve desde
el mismo dominio, por lo que el login no necesita CORS. Si vas a consumir la API
desde otro dominio, cambia `CORS_ORIGINS` por una lista separada por comas de
orígenes exactos; no uses `*` junto con credenciales.

### 3. Primer acceso del administrador

1. Abre la URL pública en Phantom o Solflare.
2. Pulsa **Conectar wallet** y firma el challenge; no se comparte ninguna private key.
3. Como tu wallet está en `ADMIN_WALLETS`, tendrás acceso y verás **Panel Admin**.
4. Añade las wallets de tu comunidad desde la whitelist, o permite que cada persona
   pague `ACCESS_PRICE_SOL` SOL a `MERCHANT_WALLET`.
5. Tras el pago, la persona pulsa **Ya pagué — Verificar**. Helius comprueba la
   transferencia confirmada y concede `ACCESS_DURATION_DAYS` días.

### 4. Comprobaciones después del despliegue

```bash
curl https://TU-URL.onrender.com/api/health
# Debe devolver: {"status":"healthy", ...}
```

Prueba también una wallet autorizada y una wallet no autorizada. Una petición a
cualquier ruta protegida sin `Authorization: Bearer <sesión>` debe responder `401`.
No actives `DEMO_MODE` ni `ALLOW_SERVER_SIDE_TRADING` en producción.

### Límites y operación

- La autenticación actual guarda las sesiones en memoria: un reinicio obliga a volver a firmar,
  pero whitelist y pagos sobreviven en el disco persistente.
- Para unas 100 personas, Helius debe tener margen suficiente y el rate limiter/caché
  reducen las llamadas repetidas. Vigila el uso en el panel de Helius.
- La ejecución de swaps con private keys está desactivada en producción. Nunca pegues
  una private key en la web; el siguiente paso seguro es firmar la transacción localmente
  con Phantom/Solflare.

## Mejora continua (feedback de la comunidad)

Cada recomendación del dashboard tiene botones 👍/👎. Los votos se guardan en `data/feedback.json`
y el sistema aprende qué señales son útiles para tu comunidad, reordenando las
recomendaciones de futuros análisis (las señales con más votos positivos suben de posición).

- `POST /api/feedback/vote?signal_type=...&useful=true&wallet_address=...` — registrar voto
- `GET /api/feedback/stats` — qué señales son más útiles

> El archivo `data/feedback.json` está en `.gitignore`: en producción apunta a un volumen
> persistente para que los votos sobrevivan a los reinicios del servidor.

## Graft (desarrollo de agentes de IA)

El repo incluye [Graft](https://github.com/NanoNets/Graft): construye un grafo de markdown
con explicaciones del código para que los agentes de IA (Claude Code, Cursor, Codex)
entiendan el proyecto sin explorarlo desde cero. No es un componente de la app en producción:
mejora la velocidad y calidad del desarrollo.

```bash
npx -y @nanonets/graft build        # reconstruir el grafo tras cambios
npx -y @nanonets/graft ask "..."     # preguntar al grafo sobre el código
```

El grafo vive en `graft/` (git-ignored, regenerable). El wiring está en `.claude/` y `.mcp.json`.

## Roadmap

- [ ] Alertas en tiempo real con WebSockets
- [ ] Comparación con otros traders
- [ ] Backtesting de estrategias
- [ ] Valoración USD de holdings con precios on-chain
