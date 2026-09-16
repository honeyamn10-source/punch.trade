# punch.trade end-to-end smoke test (Windows PowerShell).
# Boots the server on a test token/port, exercises every subsystem over HTTP,
# prints PASS/FAIL per check and exits non-zero on any failure.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/smoke.ps1
# Env:    PUNCH_SMOKE_PORT (default 8000), PUNCH_SMOKE_TOKEN (default smoke-test-token)

$ErrorActionPreference = "Stop"
$port = if ($env:PUNCH_SMOKE_PORT) { $env:PUNCH_SMOKE_PORT } else { 8000 }
$token = if ($env:PUNCH_SMOKE_TOKEN) { $env:PUNCH_SMOKE_TOKEN } else { "smoke-test-token-123" }
$base = "http://127.0.0.1:$port"
$H = @{ "X-Punch-Token" = $token }
$pass = 0; $fail = 0

function Check([string]$name, [scriptblock]$body) {
    try { & $body; $script:pass++; Write-Host "PASS  $name" -ForegroundColor Green }
    catch { $script:fail++; Write-Host "FAIL  $name -> $($_.Exception.Message)" -ForegroundColor Red }
}

$server = $null
try {
    $env:PUNCH_TOKEN = $token
    $env:PUNCH_DB_PATH = Join-Path $env:TEMP "punch-smoke-$PID.db"
    $out = Join-Path $env:TEMP "punch-smoke-$PID.out.log"
    $err = Join-Path $env:TEMP "punch-smoke-$PID.err.log"
    Remove-Item $out, $err -ErrorAction SilentlyContinue
    $server = Start-Process python -ArgumentList "run.py" -WorkingDirectory (Join-Path $PSScriptRoot "..") `
        -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 8

    Check "health (no auth required)" { Invoke-RestMethod "$base/api/health" -TimeoutSec 10 | Out-Null }
    Check "auth rejects bad token" {
        $code = $null
        try { Invoke-WebRequest "$base/api/strategies" -Headers @{"X-Punch-Token"="wrong"} -UseBasicParsing -TimeoutSec 10 | Out-Null }
        catch { $code = $_.Exception.Response.StatusCode.value__ }
        if ($code -ne 401) { throw "expected 401, got $code" }
    }
    Check "auth accepts token header" { Invoke-RestMethod "$base/api/strategies" -Headers $H -TimeoutSec 10 | Out-Null }
    Check "error envelope on unknown signal" {
        $msg = $null
        try { Invoke-RestMethod "$base/api/orders" -Method Post -Headers $H -ContentType "application/json" `
                -Body '{"broker":"paper","symbol":"X","side":"buy","qty":1,"signalId":"nope"}' -TimeoutSec 10 | Out-Null }
        catch { $msg = $_.ErrorDetails.Message }
        if ($msg -notmatch '"error".*"code"') { throw "envelope missing: $msg" }
    }
    Check "system status" { $s = Invoke-RestMethod "$base/api/system/status" -Headers $H -TimeoutSec 10; if ($s.mode -ne "paper") { throw "mode" } }
    Check "deep health" { $h = Invoke-RestMethod "$base/api/v1/system/health" -Headers $H -TimeoutSec 10; if ($h.db.ok -ne $true) { throw "db not ok" } }
    Check "metrics" { $m = Invoke-RestMethod "$base/api/v1/system/metrics" -Headers $H -TimeoutSec 10; if (-not $m.counters) { throw "no counters" } }
    Check "storage" { $st = Invoke-RestMethod "$base/api/system/storage" -Headers $H -TimeoutSec 10; if ($st.journalMode -ne "wal") { throw "no WAL" } }
    Check "signals present" { $sl = Invoke-RestMethod "$base/api/signals/last" -Headers $H -TimeoutSec 10; if ($sl.signals.Count -lt 1) { throw "no signals" } }
    Check "strategies status ladder" { $ss = Invoke-RestMethod "$base/api/strategies/status" -Headers $H -TimeoutSec 10; if ($ss.rows.Count -lt 1) { throw "no rows" } }
    Check "backtest (honest)" {
        $b = Invoke-RestMethod "$base/api/strategies/rsi-reversal/backtest" -Method Post -Headers $H `
            -ContentType "application/json" -Body '{"broker":"paper","interval":"5m","days":30}' -TimeoutSec 30
        if (-not $b.metrics.trades) { throw "no trades" }
    }
    Check "research dossier" {
        $r = Invoke-RestMethod "$base/api/research/rsi-reversal" -Method Post -Headers $H `
            -ContentType "application/json" -Body '{}' -TimeoutSec 30
        if (-not $r.qualityGate) { throw "no quality gate" }
    }
    Check "risk state + sizing" {
        $rs = Invoke-RestMethod "$base/api/risk/state" -Headers $H -TimeoutSec 10
        $sz = Invoke-RestMethod "$base/api/risk/sizing" -Method Post -Headers $H -ContentType "application/json" `
            -Body '{"equity":1000000,"riskPct":0.01,"entry":100,"stop":99}' -TimeoutSec 10
        if ($sz.qty -lt 1) { throw "bad sizing" }
    }
    Check "place paper order -> ledger -> reconcile" {
        $o = Invoke-RestMethod "$base/api/orders" -Method Post -Headers $H -ContentType "application/json" `
            -Body '{"broker":"paper","symbol":"RELIANCE","side":"buy","qty":1,"entry":100,"targetPrice":101,"stopLoss":99}' -TimeoutSec 10
        if ($o.result.status -ne "FILLED") { throw "not filled: $($o.result.status)" }
        $l = Invoke-RestMethod "$base/api/execution/ledger" -Headers $H -TimeoutSec 10
        if ($l.orders.Count -lt 1) { throw "empty ledger" }
        $rc = Invoke-RestMethod "$base/api/execution/reconcile" -Method Post -Headers $H `
            -ContentType "application/json" -Body '{"broker":"paper"}' -TimeoutSec 10
        if ($rc.ok -ne $true) { throw "reconcile failed" }
    }
    Check "closed trades" { $t = Invoke-RestMethod "$base/api/execution/trades" -Headers $H -TimeoutSec 10; $t | Out-Null }
    Check "ai status (offline-safe)" { $a = Invoke-RestMethod "$base/api/ai/status" -Headers $H -TimeoutSec 10; $a | Out-Null }
    Check "dashboard served" { $d = Invoke-WebRequest "$base/dashboard" -UseBasicParsing -TimeoutSec 10; if ($d.StatusCode -ne 200) { throw "dashboard" } }
    Check "session login + logout" {
        $sess = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $lr = Invoke-RestMethod "$base/api/system/login" -Method Post -Headers $H -WebSession $sess -TimeoutSec 10
        if (-not $lr.session) { throw "no session" }
        $csrf = $sess.Cookies.GetCookies($base) | Where-Object Name -eq "punch_csrf" | Select-Object -ExpandProperty Value
        $lo = Invoke-RestMethod "$base/api/system/logout" -Method Post -Headers @{
            "X-Punch-Token" = $token; "X-Punch-CSRF" = $csrf } -WebSession $sess -TimeoutSec 10
        if ($lo.ok -ne $true) { throw "logout not ok" }
    }
}
catch {
    Write-Host "SMOKE ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Get-Content $err -ErrorAction SilentlyContinue | Select-Object -Last 15
    exit 1
}
finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Remove-Item $env:PUNCH_DB_PATH -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "SMOKE RESULT: $pass passed, $fail failed" -ForegroundColor Cyan
if ($fail -gt 0) { exit 1 }
exit 0