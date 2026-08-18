# render-debug.ps1 - diagnostico: muestra el cuerpo COMPLETO de la API de Render
# Uso:  powershell -ExecutionPolicy Bypass -File .\render-debug.ps1
if (-not $env:RENDER_API_KEY) {
    throw "Falta RENDER_API_KEY. Ejecuta antes: `$env:RENDER_API_KEY = Read-Host 'API key de Render'"
}
$headers = @{ Authorization = "Bearer $env:RENDER_API_KEY" }

Write-Host "=== 1) GET /services (lista) ===" -ForegroundColor Cyan
$r = Invoke-WebRequest -Method Get -Uri "https://api.render.com/v1/services" -Headers $headers -UseBasicParsing
Write-Host "STATUS: $($r.StatusCode)"
Write-Host "BODY: $($r.Content.Substring(0, [Math]::Min(500, $r.Content.Length)))"

Write-Host "`n=== 2) POST /services (el mismo payload) ===" -ForegroundColor Cyan
$supabaseUrl = Read-Host "URL Supabase (https://xxx.supabase.co)"
$serviceRole = Read-Host "Service role key"
$adminWallets = (Read-Host "Wallet admin").Trim()
$heliusKey = ""

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
        @{ key = "MERCHANT_WALLET"; value = $adminWallets },
        @{ key = "CORS_ORIGINS"; value = "https://nachoweb3.github.io/codigo-elite-beter-trader" }
    )
    serviceDetails = @{
        runtime = "docker"
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

$body = [System.Text.Encoding]::UTF8.GetBytes($payload)
try {
    $r2 = Invoke-WebRequest -Method Post -Uri "https://api.render.com/v1/services" `
        -Headers $headers -ContentType "application/json" -Body $body -UseBasicParsing
    Write-Host "STATUS: $($r2.StatusCode)"
    Write-Host "BODY: $($r2.Content)"
} catch {
    $resp = $_.Exception.Response
    if ($resp) {
        Write-Host "STATUS: $([int]$resp.StatusCode)"
        $stream = $resp.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
        $bodyText = $reader.ReadToEnd()
        Write-Host "BODY: $bodyText"
        Write-Host "`n(Diagnostico completo impreso arriba. Copia solo la linea BODY a tu asistente.)"
    } else {
        Write-Host ("ERROR SIN RESPUESTA HTTP: " + $_.Exception.Message)
    }
}