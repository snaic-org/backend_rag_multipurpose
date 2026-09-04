param(
    [string]$EnvPath = "backend/.env",
    [string]$NimBaseUrl = "https://integrate.api.nvidia.com/v1",
    [string]$RerankBaseUrl = "https://ai.api.nvidia.com/v1/retrieval"
)

$ErrorActionPreference = "Stop"

function Set-EnvValue {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )

    $prefix = "$Key="
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        if ($Lines[$index].StartsWith($prefix)) {
            $Lines[$index] = "$Key=$Value"
            return $Lines
        }
    }

    return @($Lines + "$Key=$Value")
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Env file not found: $EnvPath"
}

$lines = Get-Content -LiteralPath $EnvPath
$rerankModel = $null

foreach ($line in $lines) {
    if ($line.StartsWith("RERANK_MODEL=")) {
        $rerankModel = $line.Substring("RERANK_MODEL=".Length).Trim()
        break
    }
}

if (-not $rerankModel) {
    $rerankModel = "nvidia/llama-nemotron-rerank-vl-1b-v2"
}

$normalizedRerankModel = $rerankModel.TrimStart("/")
if ($normalizedRerankModel.StartsWith("nvidia/")) {
    $normalizedRerankModel = $normalizedRerankModel.Substring("nvidia/".Length)
}

$rerankInvokeUrl = "$RerankBaseUrl/nvidia/$normalizedRerankModel/reranking"

$lines = Set-EnvValue -Lines $lines -Key "NIM_BASE_URL" -Value $NimBaseUrl
$lines = Set-EnvValue -Lines $lines -Key "RERANK_INVOKE_URL" -Value $rerankInvokeUrl

Set-Content -LiteralPath $EnvPath -Value $lines -Encoding utf8

Write-Host "Updated $EnvPath"
Write-Host "NIM_BASE_URL=$NimBaseUrl"
Write-Host "RERANK_INVOKE_URL=$rerankInvokeUrl"
