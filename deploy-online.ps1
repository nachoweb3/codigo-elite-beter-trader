# CE BetterTrader - Despliegue online (Render Free + Supabase)
# Ejecutar en PowerShell:  powershell -ExecutionPolicy Bypass -File .\deploy-online.ps1
# Requisitos:
#   - $env:RENDER_API_KEY  (API key de Render)
#   - $env:SUPABASE_ACCESS_TOKEN  (token de acceso de Supabase)  [solo si el proyecto aun no existe]
#   - Un proyecto Supabase ya creado (puedes crearlo con la CLI de Supabase)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== CE BetterTrader: despliegue online (Render Free + Supabase) ===" -ForegroundColor Cyan

# 1) Credenciales de Render (deben estar en el entorno local, nunca en este chat)
if (-not $env:RENDER_API_KEY) {
    throw "Falta la API key de Render. Ejecuta antes: `$env:RENDER_API_KEY = Read-Host 'API key de Render'"
}
Write-Host "[OK] API key de Render cargada (no se muestra)." -ForegroundColor Green

# 1b) Validar el Blueprint con el CLI oficial de Render (instalado en .tools)
$renderCli = Join-Path $root ".tools\render-cli\render-cli.exe"
if (Test-Path $renderCli) {
    Write-Host "Preparando el CLI oficial de Render (workspace)..."
    try {
        $ws = & $renderCli workspaces -o json 2>$null | ConvertFrom-Json
        if ($ws -and $ws.Count -gt 0) {
            & $renderCli workspace set $ws[0].id --confirm 2>&1 | Out-Null
            Write-Host "[OK] Workspace activo: $($ws[0].name)" -ForegroundColor Green
        }
    } catch { Write-Warning "No se pudo configurar el workspace del CLI; sigo con la API REST." }
    Write-Host "Validando render.yaml con el CLI oficial de Render..."
    & $renderCli blueprints validate (Join-Path $root "render.yaml") -o text
    if ($LASTEXITCODE -ne 0) { Write-Warning "La validacion del CLI fallo; sigo con la API REST (asi lo cobra el script)." }
    else { Write-Host "[OK] Blueprint render.yaml valido." -ForegroundColor Green }
} else {
    Write-Warning "CLI oficial no encontrado. Descargalo previamient (la skill render-cli lo instala)."
}
$headers = @{ Authorization = "Bearer $env:RENDER_API_KEY" }

# 2) Datos de Supabase
$supabaseUrl = Read-Host "URL del proyecto Supabase (https://xxx.supabase.co)"
$serviceRole = Read-Host "Service role key de Supabase (se guarda solo como secreto en Render)"
$adminWallets = (Read-Host "Wallet publica administradora (tu wallet)").Trim()
$merchantWallet = (Read-Host "Wallet que recibe los pagos (MERCHANT_WALLET)").Trim()
$heliusKey = (Read-Host "API key de Helius (opcional, Enter para omitir)").Trim()

if (-not $supabaseUrl -or -not $serviceRole -or -not $adminWallets) {
    throw "Supabase URL, service key y wallet admin son obligatorios."
}
if (-not $merchantWallet) { $merchantWallet = $adminWallets }

# 3) Verificacion rapida de la service key contra la API de Supabase
try {
    $tables = Invoke-RestMethod -Method Get -Uri ($supabaseUrl.TrimEnd('/') + "/rest/v1/app_store?select=store_key&limit=1") -Headers @{
        apikey          = $serviceRole
        Authorization   = "Bearer $serviceRole"
    } -TimeoutSec 20
    Write-Host "[OK] Supabase accesible con la service key (tabla app_store existe)." -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {
        Write-Host "[AVISO] Supabase accesible pero la tabla 'app_store' no existe." -ForegroundColor Yellow
        Write-Host "        Ejecuta el contenido de supabase_schema.sql en Supabase > SQL Editor."
    } else {
        Write-Warning "No se pudo verificar Supabase: $($_.Exception.Message)"
        Write-Host "Continuando... (el despliegue se intentara igualmente; revisa las credenciales si falla)"
    }
}

# 4) Crear el servicio web en Render (Free)
try {
    $existing = Invoke-RestMethod -Method Get -Uri "https://api.render.com/v1/services" -Headers $headers -TimeoutSec 20
    $already = $existing | Where-Object { $_.service.name -eq "ce-bettertrader" } | Select-Object -First 1
    if ($already) {
        Write-Host "[OK] El servicio ce-bettertrader ya existe. " -ForegroundColor Green
        Write-Host "URL: $($already.service.serviceDetails.url)"
        Write-Host "Comparalo con el repositorio: si falta algo, edita el servicio en el dashboard."
        exit 0
    }
} catch { }
$body = @{
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
    serviceDetails = @{
        runtime = "docker"
        env     = "docker"
        plan    = "free"
        region  = "oregon"
        numInstances = 1
        healthCheckPath = "/api/health"
        buildPlan = "starter"
        envSpecificDetails = @{
            dockerfilePath = "./Dockerfile"
            dockerContext  = "./"
        }
    }
} | ConvertTo-Json -Depth 8

Write-Host "Creando el servicio en Render (plan free)..."
$jsonBody = $body
try {
    $resp = Invoke-RestMethod -Method Post -Uri "https://api.render.com/v1/services" `
        -Headers $headers -ContentType "application/json" -Body $jsonBody
    Write-Host "[OK] Servicio creado." -ForegroundColor Green
    Write-Host "ID:      $($resp.service.id)"
    Write-Host "Deploy:  $($resp.deployId)"
    Write-Host "URL:     $($resp.service.serviceDetails.url)"
    Write-Host "`nEl despliegue tarda unos minutos. Comprueba: $($resp.service.serviceDetails.url)/api/health"
    Write-Host "(https://dashboard.render.com/servicios)" -ForegroundColor Cyan
} catch {
    Write-Host "Error al crear el servicio en Render:" -ForegroundColor Red
    Write-Host $_.Exception.Message
    if ($_.Exception.Response) {
        try { Write-Host (New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd() } catch {}
    }
    exit 1
}

# 5) Ayuda final
Write-Host "`nPASOS RESTANTES:" -ForegroundColor Cyan
Write-Host "  1. Si usas GitHub Pages para el frontend: crea la variable CE_API_BASE_URL"
Write-Host "     con el valor de la URL de arriba (sin /api)."
Write-Host "  2. Primera visita: conecta tu wallet (la del ADMIN_WALLETS) y firma."
Write-Host "  3. Para un dominio propio: Render -> Custom Domains."