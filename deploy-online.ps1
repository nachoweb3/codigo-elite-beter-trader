# CE BetterTrader - Despliegue online (Render Free + Supabase)
# Ejecutar en PowerShell:  powershell -ExecutionPolicy Bypass -File .\deploy-online.ps1
# Requisitos:
#   - $env:RENDER_API_KEY  (API key de Render)
#   - Proyecto Supabase ya creado

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Read-ErrorBody($ex) {
    if ($ex.Exception.Response) {
        try {
            $stream = $ex.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            return $reader.ReadToEnd()
        } catch { }
    }
    return $ex.Exception.Message
}

Write-Host "=== CE BetterTrader: despliegue online (Render Free + Supabase) ===" -ForegroundColor Cyan

# 1) Credenciales de Render (entorno local, nunca en el chat)
if (-not $env:RENDER_API_KEY) {
    throw "Falta la API key de Render. Ejecuta antes: `$env:RENDER_API_KEY = Read-Host 'API key de Render'"
}
Write-Host "[OK] API key de Render cargada (no se muestra)." -ForegroundColor Green

# 1b) Validar Blueprint con el CLI oficial (opcional, no bloquea)
$renderCli = Join-Path $root ".tools\render-cli\render-cli.exe"
if (Test-Path $renderCli) {
    try {
        $ws = & $renderCli workspaces -o json 2>$null | ConvertFrom-Json
        if ($ws -and $ws.Count -gt 0) {
            & $renderCli workspace set $ws[0].id --confirm 2>&1 | Out-Null
            Write-Host "[OK] Workspace activo del CLI: $($ws[0].name)" -ForegroundColor Green
        }
        & $renderCli blueprints validate (Join-Path $root "render.yaml") -o text
        if ($LASTEXITCODE -eq 0) { Write-Host "[OK] Blueprint valido." -ForegroundColor Green }
        else { Write-Warning "Validacion del CLI fallo; continuo con la API REST." }
    } catch { Write-Warning "CLI no disponible; continuo con la API REST." }
}

# 2) Datos de la app
$supabaseUrl = Read-Host "URL del proyecto Supabase (https://xxx.supabase.co)"
$serviceRole = Read-Host "Service role key de Supabase"
$adminWallets = (Read-Host "Wallet publica administradora").Trim()
$merchantWallet = (Read-Host "Wallet que recibe pagos").Trim()
$heliusKey = (Read-Host "API key de Helius (opcional, Enter para omitir)").Trim()

if (-not $supabaseUrl -or -not $serviceRole -or -not $adminWallets) {
    throw "Supabase URL, service key y wallet admin son obligatorios."
}
if (-not $merchantWallet) { $merchantWallet = $adminWallets }
$supabaseUrl = $supabaseUrl.TrimEnd('/')

# 3) Verificar acceso a Supabase y avisar si falta la tabla app_store
# (PostgREST no ejecuta DDL; la tabla se crea una sola vez en SQL Editor).
try {
    Invoke-RestMethod -Method Get -Uri "$supabaseUrl/rest/v1/app_store?select=store_key&limit=1" -Headers @{
        apikey        = $serviceRole
        Authorization = "Bearer $serviceRole"
    } -TimeoutSec 20 | Out-Null
    Write-Host "[OK] Supabase accesible y tabla app_store presente." -ForegroundColor Green
} catch {
    Write-Host "[PENDIENTE] La tabla app_store no existe aun. (No bloquea: se crea en 1 minuto.)" -ForegroundColor Yellow
    Write-Host "  1. Abre https://supabase.com/dashboard  >  tu proyecto  >  SQL Editor"
    Write-Host "  2. Pega el contenido de supabase_schema.sql (esta carpeta) y pulsa Run."
    Write-Host "  3. La app ya funcionara con este script aunque sigas ahora."
}

# 3b) Obtener el workspace (ownerId) de la API key
$headers = @{ Authorization = "Bearer $env:RENDER_API_KEY" }
$ownerId = $null
try {
    $owners = Invoke-RestMethod -Method Get -Uri "https://api.render.com/v1/owners" -Headers $headers -TimeoutSec 20
    if ($owners.Count -gt 0) { $ownerId = $owners[0].owner.id }
} catch { }
if (-not $ownerId) { throw "No se pudo obtener el workspace de Render con esta API key." }
Write-Host "[OK] Workspace de Render: $ownerId" -ForegroundColor Green

# 4) Crear servicio en Render (Free)

try {
    $existing = Invoke-RestMethod -Method Get -Uri "https://api.render.com/v1/services" -Headers $headers -TimeoutSec 20
    $already = $existing | Where-Object { $_.service.name -eq "ce-bettertrader" } | Select-Object -First 1
    if ($already) {
        Write-Host "[OK] El servicio ce-bettertrader ya existe en Render." -ForegroundColor Green
        Write-Host "URL: $($already.service.serviceDetails.url)"
        exit 0
    }
} catch { Write-Warning "No se pudieron listar servicios (puede que la key no tenga permisos): $(Read-ErrorBody $_)" }

$payload = @{
    type  = "web_service"
    name  = "ce-bettertrader"
    repo  = "https://github.com/nachoweb3/codigo-elite-beter-trader"
    branch = "main"
    autoDeploy = "yes"
    envVars = @(
        @{ key = "ACCESS_CONTROL"; value = "true" },
        @{ key = "DEMO_MODE"; value = "false" },
        @{ key = "ALLOW_SERVER_SIDE_TRADING"; value = "false" },
        @{ key = "ACCESS_PRICE_SOL"; value = "0.1" },
        @{ key = "ACCESS_DURATION_DAYS"; value = "30" },
        @{ key = "HELIUS_API_KEY"; value = $heliusKey },
        @{ key = "SUPABASE_URL"; value = $supabaseUrl },
        @{ key = "SUPABASE_SERVICE_ROLE_KEY"; value = $serviceRole },
        @{ key = "ADMIN_WALLETS"; value = $adminWallets },
        @{ key = "MERCHANT_WALLET"; value = $merchantWallet },
        @{ key = "CORS_ORIGINS"; value = "https://nachoweb3.github.io/codigo-elite-beter-trader" }
    )
    runtime = "docker"
    ownerId = $ownerId
    serviceDetails = @{
        env     = "docker"
        plan    = "free"
        region  = "oregon"
        numInstances = 1
        healthCheckPath = "/api/health"
        envSpecificDetails = @{
            dockerfilePath = "./Dockerfile"
            dockerContext  = "./"
        }
    }
} | ConvertTo-Json -Depth 8

Write-Host "Creando el servicio en Render (plan free)..."
try {
    $resp = Invoke-RestMethod -Method Post -Uri "https://api.render.com/v1/services" `
        -Headers $headers -ContentType "application/json" -Body $payload
    Write-Host "[OK] Servicio creado." -ForegroundColor Green
    Write-Host "ID:  $($resp.service.id)"
    Write-Host "URL: $($resp.service.serviceDetails.url)"
    Write-Host "Deploy: $($resp.deployId)  (estado en https://dashboard.render.com)"
    Write-Host "`nComprueba en 2-5 minutos: $($resp.service.serviceDetails.url)/api/health"
} catch {
    Write-Host "ERROR (400/Error) - detalle completo:" -ForegroundColor Red
    Write-Host (Read-ErrorBody $_)
    exit 1
}

Write-Host "`nFIN. Si el servicio quedo creado, el frontend de GitHub Pages usa CE_API_BASE_URL." -ForegroundColor Cyan