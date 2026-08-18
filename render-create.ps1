# render-create.ps1 - aislar el 400 de Render
# Prueba 1: servicio MINIMAL (sin envVars, sin branch, sin helius)
# Prueba 2: servicio COMPLETO (igual que deploy-online.ps1)
if (-not $env:RENDER_API_KEY) { throw "Falta RENDER_API_KEY" }
$headers = @{ Authorization = "Bearer $env:RENDER_API_KEY" }

function Test-Create($label, $json) {
    Write-Host "`n=== $label ===" -ForegroundColor Cyan
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $r = Invoke-WebRequest -Method Post -Uri "https://api.render.com/v1/services" `
            -Headers $headers -ContentType "application/json" -Body $bytes -UseBasicParsing
        Write-Host "STATUS: $($r.StatusCode)"
        $content = $r.Content
        if ($content) { Write-Host "BODY: $content" }
        $jsonResp = $content | ConvertFrom-Json
        if ($jsonResp.service.id) {
            Write-Host "SERVIDOR CREADO. URL: $($jsonResp.service.serviceDetails.url)  ID: $($jsonResp.service.id)"
        }
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            Write-Host "STATUS: $([int]$resp.StatusCode)"
            $stream = $resp.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $bodyText = $reader.ReadToEnd()
            Write-Host "BODY: $bodyText"
            if (-not $bodyText) { Write-Host "BODY: (vacío)" }
        } else {
            Write-Host ("ERROR: " + $_.Exception.Message)
        }
    }
}

# Obtener ownerId (workspace) una sola vez
$owners = Invoke-RestMethod -Method Get -Uri "https://api.render.com/v1/owners" -Headers $headers -TimeoutSec 20
$ownerId = $owners[0].owner.id
Write-Host "Workspace: $ownerId" -ForegroundColor Cyan

# --- Prueba 1: minimal (con ownerId + runtime a nivel superior) ---
$min = @{
    type  = "web_service"
    name  = "ce-bettertrader-test-minimal"
    ownerId = $ownerId
    runtime = "docker"
    serviceDetails = @{
        env    = "docker"
        plan   = "free"
        region = "oregon"
    }
} | ConvertTo-Json -Depth 5
Test-Create "Prueba 1: minimal (con ownerId + runtime top-level)" $min

# --- Prueba 2: completo (payload actual) ---
$supabaseUrl = Read-Host "`nURL Supabase"
$serviceRole = Read-Host "Service role key"
$admin = (Read-Host "Wallet admin").Trim()

$full = @{
    type  = "web_service"
    name  = "ce-bettertrader"
    ownerId = $ownerId
    repo  = "https://github.com/nachoweb3/codigo-elite-beter-trader"
    branch = "main"
    autoDeploy = "yes"
    runtime = "docker"
    envVars = @(
        @{ key = "ACCESS_CONTROL"; value = "true" },
        @{ key = "DEMO_MODE"; value = "false" },
        @{ key = "ALLOW_SERVER_SIDE_TRADING"; value = "false" },
        @{ key = "ACCESS_PRICE_SOL"; value = "0.1" },
        @{ key = "ACCESS_DURATION_DAYS"; value = "30" },
        @{ key = "HELIUS_API_KEY"; value = "" },
        @{ key = "SUPABASE_URL"; value = $supabaseUrl },
        @{ key = "SUPABASE_SERVICE_ROLE_KEY"; value = $serviceRole },
        @{ key = "ADMIN_WALLETS"; value = $admin },
        @{ key = "MERCHANT_WALLET"; value = $admin },
        @{ key = "CORS_ORIGINS"; value = "https://nachoweb3.github.io/codigo-elite-beter-trader" }
    )
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
Test-Create "Prueba 2: completo (igual que deploy-online)" $full