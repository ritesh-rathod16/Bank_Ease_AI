param(
    [switch]$Pull,
    [switch]$StartServer,
    [string]$Model = "qwen3:4b-instruct"
)

$ErrorActionPreference = "Stop"

Write-Host "BankEase AI local model check"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PortableDirectory = Join-Path $ProjectRoot "data\runtime\ollama\windows"
$PortableCandidates = @(
    (Join-Path $PortableDirectory "ollama.exe"),
    (Join-Path $PortableDirectory "ollama\ollama.exe"),
    (Join-Path $PortableDirectory "bin\ollama.exe")
)
$PortableOllama = $PortableCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($PortableOllama) {
    $OllamaPath = $PortableOllama
    $env:PATH = "$PortableDirectory;$env:PATH"
    # Ollama creates its signing key under the user home. Keep that key local too.
    $env:USERPROFILE = Join-Path $ProjectRoot "data\runtime\ollama\profile"
    $env:OLLAMA_MODELS = Join-Path $ProjectRoot "data\runtime\ollama\models"
    $env:OLLAMA_NO_CLOUD = "1"
    New-Item -ItemType Directory -Force -Path $env:USERPROFILE | Out-Null
    New-Item -ItemType Directory -Force -Path $env:OLLAMA_MODELS | Out-Null
    Write-Host "Using the workspace-local Ollama runtime. Models stay under data/runtime/ollama/models."
} else {
    $Ollama = Get-Command ollama -ErrorAction SilentlyContinue
    $OllamaPath = if ($Ollama) { $Ollama.Source } else { $null }
}
if (-not $OllamaPath) {
    Write-Host "Ollama is not installed or not on PATH. Install it from https://ollama.com/download/windows, then reopen PowerShell."
    exit 1
}

$Version = & $OllamaPath --version
if ($LASTEXITCODE -ne 0) { throw "Ollama failed to report its version." }
Write-Host $Version
try {
    $Tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
} catch {
    if (-not $StartServer) {
        Write-Host "Ollama is installed but its local API is not responding. Start it or rerun with -StartServer."
        exit 1
    }
    Write-Host "Starting the local Ollama API."
    Start-Process -FilePath $OllamaPath -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    $Deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $Tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
            break
        } catch {
            if ((Get-Date) -ge $Deadline) {
                throw "Ollama did not start its local API within 30 seconds."
            }
        }
    } while ($true)
}

$Installed = @($Tags.models | ForEach-Object { $_.name })
if ($Installed -contains $Model) {
    Write-Host "Configured model '$Model' is already installed."
    exit 0
}

if (-not $Pull) {
    Write-Host "Model '$Model' is not installed. No model has been downloaded."
    Write-Host "After reviewing its license and disk/RAM needs, run: .\scripts\setup_ollama.ps1 -StartServer -Pull -Model '$Model'"
    exit 0
}

Write-Host "Pulling '$Model' because -Pull was explicitly supplied."
& $OllamaPath pull $Model
if ($LASTEXITCODE -ne 0) { throw "Ollama could not finish pulling '$Model'. Rerun the command to resume." }
