$ErrorActionPreference = "Stop"
Set-Location "C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002"
$token = [Environment]::GetEnvironmentVariable("OEMIXIBOT_TOKEN", "User")
if ([string]::IsNullOrWhiteSpace($token)) { throw "OEMIXIBOT_TOKEN User variable is missing" }
$env:OEMIXIBOT_TOKEN = $token.Trim()
$env:EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT = [Environment]::GetEnvironmentVariable("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT","User")
if (-not $env:EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT) { throw "EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT is not configured" }
$env:OEMIXIBOT_DB_PATH = Join-Path $PWD "oemixibot_private.db"
Write-Output ("OEMixiBOT token loaded safely; length=" + $env:OEMIXIBOT_TOKEN.Length)
Write-Output ("OEMixiBOT DB=" + $env:OEMIXIBOT_DB_PATH)
python -B oemixibot_app.py
