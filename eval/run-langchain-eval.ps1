# Edit `eval/config.py` for dataset, model, sample size, and judge defaults.
# Use this wrapper mainly for:
#   .\eval\run-langchain-eval.ps1
#   .\eval\run-langchain-eval.ps1 -BaseUrl http://localhost:9010
#   .\eval\run-langchain-eval.ps1 -Username admin -Password change-me-immediately

param(
    [string]$BaseUrl = "http://localhost:9010/",
    [string]$Username = "",
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$backendEnvPath = Join-Path $repoRoot "backend\.env"
$configJson = & python -c "import json, pathlib, sys; sys.path.insert(0, str(pathlib.Path(r'$scriptDir'))); import config; print(json.dumps(config.EVAL_DEFAULTS))"
$evalConfig = $configJson | ConvertFrom-Json

function Get-EnvMap {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        $values[$key] = $value
    }

    return $values
}

$backendEnv = Get-EnvMap -Path $backendEnvPath

if (-not $PSBoundParameters.ContainsKey("BaseUrl") -or -not $BaseUrl) {
    $port = if ($backendEnv.ContainsKey("APP_PORT")) { $backendEnv["APP_PORT"] } else { "9010" }
    $BaseUrl = "http://localhost:$port"
}

if (-not $Username) {
    $Username = if ($backendEnv.ContainsKey("AUTH_BOOTSTRAP_ADMIN_USERNAME")) {
        $backendEnv["AUTH_BOOTSTRAP_ADMIN_USERNAME"]
    }
    else {
        "admin"
    }
}

if (-not $Password -and $backendEnv.ContainsKey("AUTH_BOOTSTRAP_ADMIN_PASSWORD")) {
    $Password = $backendEnv["AUTH_BOOTSTRAP_ADMIN_PASSWORD"]
}

if (-not $Password) {
    throw "Password is required. Pass -Password or set AUTH_BOOTSTRAP_ADMIN_PASSWORD in backend\.env."
}

$arguments = @(
    (Join-Path $scriptDir "langchain_eval.py"),
    "--base-url", $BaseUrl,
    "--username", $Username,
    "--password", $Password
)

Write-Host "Running LangChain evaluation against $BaseUrl"
& python @arguments
exit $LASTEXITCODE
