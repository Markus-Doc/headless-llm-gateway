# Start the gateway on 127.0.0.1 only.
# LiteLLM's own default host is 0.0.0.0 (every network interface), which would
# expose the subscription routes to the local network. This script never passes
# anything but 127.0.0.1.
param(
    [int]$Port = 4000
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    throw ".env not found. Copy .env.example to .env and fill in the values."
}
$masterLine = Select-String -Path $envFile -Pattern '^\s*LITELLM_MASTER_KEY\s*=\s*sk-\S+' -Quiet
if (-not $masterLine -and -not ($env:LITELLM_MASTER_KEY -like "sk-*")) {
    throw "LITELLM_MASTER_KEY is missing or does not start with sk-. Without it the proxy accepts any caller."
}

$env:PYTHONPATH = $root                    # lets config.yaml load gateway.* handlers
$env:LITELLM_LOCAL_MODEL_COST_MAP = "True" # use the price map shipped in the pinned package, no download at start

& (Join-Path $root ".venv\Scripts\litellm.exe") --config (Join-Path $root "config.yaml") --host 127.0.0.1 --port $Port
